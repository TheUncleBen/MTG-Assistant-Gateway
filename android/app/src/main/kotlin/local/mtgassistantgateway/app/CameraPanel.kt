package local.mtgassistantgateway.app

import android.os.Build
import android.os.Handler
import android.view.ContextThemeWrapper
import android.util.Size
import android.view.LayoutInflater
import android.view.Gravity
import android.view.Surface
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.SeekBar
import android.widget.TextView
import android.widget.ToggleButton
import androidx.activity.ComponentActivity
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.CameraState
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.ZoomState
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.lifecycle.Observer
import androidx.window.layout.FoldingFeature
import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutionException

/**
 * Phone-camera scan: a CameraX viewfinder laid over the gateway's /scan page, with the controls a
 * browser page cannot offer.
 *
 * - Torch on/off (`CameraControl.enableTorch`).
 * - Torch brightness, where the camera reports it (`CameraInfo.isTorchStrengthSupported`, which
 *   CameraX answers on Android 15 and later for cameras with more than one level). Elsewhere the
 *   slider is hidden.
 * - Zoom and exposure compensation, within what `CameraInfo` reports.
 *
 * The panel is a view inside [MainActivity], not its own activity, so the page in the WebView
 * underneath keeps running while the camera is up: every Shoot hands one JPEG to the page through
 * [Host.photo], the page reads it as it would a photo from its own Photo button, and what it did
 * comes back through [onScanEvent] as a line over the viewfinder, with Undo after an automatic
 * add. Auto repeats Shoot whenever the page has finished with the previous photo, which is the
 * page's continuous mode with the phone's camera: clean reads are added without a tap, the same
 * card is not added twice while it stays in view, and shaky reads wait on the page for Done.
 *
 * On a foldable held half open the viewfinder and the controls go on opposite sides of the hinge
 * ([onFold], [FoldSplit]): tabletop puts the viewfinder on the upper half and the controls on the
 * flat lower half; book puts them side by side.
 *
 * Threading: CameraX calls back on the main executor, so everything here runs on the UI thread.
 */
class CameraPanel(
    private val activity: ComponentActivity,
    parent: ViewGroup,
    private val host: Host,
    /** The nonce the page's glue was installed with; events without it are ignored. */
    private val nonce: String,
) {

    interface Host {
        /** One JPEG to hand to the page, with the dashed box as fractions of it (null when unknown). */
        fun photo(jpeg: ByteArray, guide: FloatArray?)
        /** The page's continuous-mode rules on or off for the photos that follow. */
        fun continuous(on: Boolean)
        /** Take the last automatic add back. */
        fun undo()
        /** The person tapped Done. */
        fun close()
    }

    // Inflated with the dark camera theme whatever the phone's setting: the panel sits over a black
    // viewfinder, and the light theme's dark text was unreadable on it.
    val view: View = LayoutInflater.from(ContextThemeWrapper(activity, R.style.Theme_MtgAssistantGateway_Camera))
        .inflate(R.layout.camera_panel, parent, false)
    private val viewfinder: FrameLayout = view.findViewById(R.id.viewfinder)
    private val previewView: PreviewView = view.findViewById(R.id.preview)
    private val guideView: CardGuideView = view.findViewById(R.id.guide)
    private val controls: View = view.findViewById(R.id.controls)
    private val status: TextView = view.findViewById(R.id.camera_status)
    private val result: TextView = view.findViewById(R.id.result_text)
    private val undo: Button = view.findViewById(R.id.undo)
    private val torchToggle: ToggleButton = view.findViewById(R.id.torch_toggle)
    private val torchStrength: SeekBar = view.findViewById(R.id.torch_strength)
    private val torchLabel: TextView = view.findViewById(R.id.torch_label)
    private val zoomBar: SeekBar = view.findViewById(R.id.zoom)
    private val zoomRow: View = view.findViewById(R.id.zoom_row)
    private val exposureBar: SeekBar = view.findViewById(R.id.exposure)
    private val exposureRow: View = view.findViewById(R.id.exposure_row)
    private val autoToggle: ToggleButton = view.findViewById(R.id.auto_toggle)
    private val shoot: Button = view.findViewById(R.id.shoot)

    private var provider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var preview: Preview? = null
    private var imageCapture: ImageCapture? = null
    private var binding = false
    private var resumed = false
    private var closed = false

    private var flashAvailable = false
    private var torchMax = 1
    private var torchLevel = 1
    private var zoomMin = 1f
    private var zoomMax = 1f
    private var zoom = 1f
    private var aeLow = 0
    private var aeHigh = 0
    private var exposure = 0
    private var capturing = false

    /** A photo is with the page until its `done` event (or the watchdog) comes back. */
    private var awaitingPage = false
    /** The page's sequence number for the photo in flight; -1 until the page has accepted it. */
    private var expectedSeq = -1
    private var canUndo = false
    private var fold: FoldingFeature? = null
    private var lastSplit: FoldSplit.Split? = null
    private var postureApplied = false
    /** A one-shot zoom-range observer registered with observeForever, dropped on close or rebind. */
    private var zoomObserver: Observer<ZoomState>? = null
    private var zoomObserved: androidx.lifecycle.LiveData<ZoomState>? = null
    private val ui = Handler(activity.mainLooper)
    private val watchdog = Runnable { pageTimedOut() }
    private val captureWatchdog = Runnable { if (capturing) captureFailed(-5) }
    private val nextAuto = Runnable { if (autoToggle.isChecked && !awaitingPage && !capturing) capture() }

    init {
        view.isClickable = true // nothing falls through to the page underneath
        previewView.scaleType = PreviewView.ScaleType.FILL_CENTER // centre crop: what the guide box assumes
        view.findViewById<Button>(R.id.done).setOnClickListener { host.close() }
        undo.setOnClickListener { if (canUndo) { canUndo = false; undo.isEnabled = false; host.undo() } }
        torchToggle.setOnCheckedChangeListener { _, _ -> applyTorch() }
        torchStrength.max = STRENGTH_STEPS - 1
        torchStrength.contentDescription = activity.getString(R.string.torch_strength) // not the exposure slider's name
        torchStrength.setOnSeekBarChangeListener(onChange { pos ->
            torchLevel = TorchMath.levelFor(pos, STRENGTH_STEPS, torchMax)
            showTorchLevel()
            if (!torchToggle.isChecked) torchToggle.isChecked = true else applyTorch()
        })
        zoomBar.max = ZOOM_STEPS - 1
        zoomBar.setOnSeekBarChangeListener(onChange { pos ->
            zoom = TorchMath.zoomFor(pos, ZOOM_STEPS, zoomMin, zoomMax)
            camera?.cameraControl?.setZoomRatio(zoom)
        })
        exposureBar.setOnSeekBarChangeListener(onChange { pos ->
            exposure = TorchMath.exposureFor(pos, aeLow, aeHigh)
            camera?.cameraControl?.setExposureCompensationIndex(exposure)
        })
        autoToggle.setOnCheckedChangeListener { _, on ->
            host.continuous(on)
            if (on) {
                showResult(activity.getString(R.string.auto_on), false)
                if (!awaitingPage && !capturing) capture()
            } else {
                ui.removeCallbacks(nextAuto)
                showResult(activity.getString(R.string.auto_off), canUndo)
            }
        }
        shoot.setOnClickListener { capture() }
        view.addOnLayoutChangeListener { _, l, t, r, b, ol, ot, or, ob ->
            if (r - l != or - ol || b - t != ob - ot) applyPosture()
        }
    }

    // -- lifecycle, driven by MainActivity --------------------------------------

    fun resume() {
        if (closed) return
        resumed = true
        openCamera()
    }

    fun pause() {
        resumed = false
        ui.removeCallbacks(nextAuto)
        closeCamera()
    }

    /**
     * Final: the view is about to leave the window. [onClosed] runs on the UI thread once CameraX
     * reports the device closed (so the page's own camera can open it), or after [CLOSE_TIMEOUT_MS].
     */
    fun destroy(onClosed: () -> Unit) {
        closed = true
        val state = camera?.cameraInfo?.cameraState
        pause()
        ui.removeCallbacks(watchdog)
        ui.removeCallbacks(captureWatchdog)
        if (autoToggle.isChecked) autoToggle.isChecked = false
        if (state == null || state.value?.type == CameraState.Type.CLOSED) {
            onClosed()
            return
        }
        var done = false
        lateinit var observer: Observer<CameraState>
        val finish = Runnable {
            if (done) return@Runnable
            done = true
            state.removeObserver(observer)
            onClosed()
        }
        observer = Observer { cs -> if (cs.type == CameraState.Type.CLOSED) finish.run() }
        state.observeForever(observer)
        ui.postDelayed(finish, CLOSE_TIMEOUT_MS)
    }

    // -- camera lifecycle -----------------------------------------------------

    private fun openCamera() {
        if (camera != null || binding || closed) return
        binding = true
        val future = ProcessCameraProvider.getInstance(activity)
        future.addListener({
            binding = false
            val p = try {
                future.get()
            } catch (e: ExecutionException) {
                status.text = activity.getString(R.string.camera_error, -1)
                return@addListener
            } catch (e: InterruptedException) {
                return@addListener
            }
            provider = p
            if (resumed && !closed) bind(p)
        }, activity.mainExecutor)
    }

    private fun bind(p: ProcessCameraProvider) {
        val backCamera = try {
            p.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)
        } catch (e: Exception) {
            false // CameraInfoUnavailableException and the like
        }
        if (!backCamera) {
            status.text = activity.getString(R.string.camera_none)
            shoot.isEnabled = false
            return
        }
        val ratio = AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY
        val pv = Preview.Builder()
            .setResolutionSelector(
                ResolutionSelector.Builder().setAspectRatioStrategy(ratio)
                    .setResolutionStrategy(ResolutionStrategy(Size(1920, 1440), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
                    .build(),
            )
            .build()
        val ic = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
            .setJpegQuality(92)
            .setResolutionSelector(
                ResolutionSelector.Builder().setAspectRatioStrategy(ratio)
                    .setResolutionStrategy(ResolutionStrategy(Size(MAX_JPEG_SIDE, MAX_JPEG_SIDE * 3 / 4), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
                    .build(),
            )
            .setTargetRotation(displayRotation())
            .build()
        val cam = try {
            p.unbindAll()
            p.bindToLifecycle(activity, CameraSelector.DEFAULT_BACK_CAMERA, pv, ic)
        } catch (e: IllegalArgumentException) {
            status.text = activity.getString(R.string.camera_error, -2)
            return
        } catch (e: IllegalStateException) {
            status.text = activity.getString(R.string.camera_error, -3)
            return
        }
        pv.setSurfaceProvider(previewView.surfaceProvider)
        camera = cam
        preview = pv
        imageCapture = ic
        capturing = false
        readCapabilities(cam)
        applyTorch()
        cam.cameraControl.setZoomRatio(zoom)
        if (aeHigh > aeLow) cam.cameraControl.setExposureCompensationIndex(exposure)
        shoot.isEnabled = !awaitingPage
        if (autoToggle.isChecked && !awaitingPage) capture()
    }

    /** "Torch 3 of 5" next to the slider, and as what TalkBack reads for its state. */
    private fun showTorchLevel() {
        val text = activity.getString(R.string.torch_level, torchLevel, torchMax)
        torchLabel.text = text
        if (Build.VERSION.SDK_INT >= 30) torchStrength.stateDescription = text
    }

    private fun readCapabilities(cam: Camera) {
        val info = cam.cameraInfo
        flashAvailable = info.hasFlashUnit()
        torchMax = if (flashAvailable && info.isTorchStrengthSupported) maxOf(1, info.maxTorchStrengthLevel) else 1
        torchLevel = (info.torchStrengthLevel.value ?: torchMax).coerceIn(1, torchMax)
        val zs = info.zoomState.value
        if (zs != null) {
            zoomMin = zs.minZoomRatio
            zoomMax = zs.maxZoomRatio
        } else {
            // Not known yet: take the first value CameraX publishes, then stop listening.
            dropZoomObserver()
            val live = info.zoomState
            val obs = Observer<ZoomState> { value ->
                dropZoomObserver()
                if (camera !== cam || closed) return@Observer
                zoomMin = value.minZoomRatio
                zoomMax = value.maxZoomRatio
                showZoom()
            }
            zoomObserver = obs
            zoomObserved = live
            live.observeForever(obs)
        }
        val es = info.exposureState
        if (es.isExposureCompensationSupported) {
            aeLow = es.exposureCompensationRange.lower
            aeHigh = es.exposureCompensationRange.upper
        } else {
            aeLow = 0
            aeHigh = 0
        }

        torchToggle.visibility = if (flashAvailable) View.VISIBLE else View.GONE
        val strength = flashAvailable && torchMax > 1
        torchStrength.visibility = if (strength) View.VISIBLE else View.GONE
        torchLabel.visibility = if (strength) View.VISIBLE else View.GONE
        if (strength) {
            torchStrength.progress = Math.round((torchLevel - 1).toFloat() * (STRENGTH_STEPS - 1) / (torchMax - 1))
            showTorchLevel()
        }
        showZoom()
        exposureRow.visibility = if (aeHigh > aeLow) View.VISIBLE else View.GONE
        exposureBar.max = maxOf(0, aeHigh - aeLow)
        exposure = exposure.coerceIn(aeLow, aeHigh)
        exposureBar.progress = TorchMath.exposurePosition(exposure, aeLow, aeHigh)

        status.text = when {
            !flashAvailable -> activity.getString(R.string.status_no_flash)
            strength -> activity.getString(R.string.status_strength)
            android.os.Build.VERSION.SDK_INT < 35 -> activity.getString(R.string.status_no_strength_old)
            else -> activity.getString(R.string.status_no_strength_device)
        }
    }

    private fun showZoom() {
        zoomRow.visibility = if (zoomMax > zoomMin) View.VISIBLE else View.GONE
        zoom = zoom.coerceIn(zoomMin, zoomMax)
        if (zoomMax > zoomMin) zoomBar.progress = Math.round((zoom - zoomMin) / (zoomMax - zoomMin) * (ZOOM_STEPS - 1))
    }

    private fun dropZoomObserver() {
        val obs = zoomObserver ?: return
        zoomObserved?.removeObserver(obs)
        zoomObserver = null
        zoomObserved = null
    }

    private fun closeCamera() {
        capturing = false
        shoot.isEnabled = false
        ui.removeCallbacks(captureWatchdog)
        dropZoomObserver()
        val p = provider
        camera = null
        preview = null
        imageCapture = null
        p?.unbindAll() // CameraX closes the device on its own thread and hands the lens back
    }

    // -- controls -------------------------------------------------------------

    private fun applyTorch() {
        val cam = camera ?: return
        if (!flashAvailable) return
        val on = torchToggle.isChecked
        if (on && torchMax > 1) cam.cameraControl.setTorchStrengthLevel(torchLevel.coerceIn(1, torchMax))
        cam.cameraControl.enableTorch(on)
    }

    private fun capture() {
        val ic = imageCapture ?: return
        if (capturing || awaitingPage) return
        capturing = true
        shoot.isEnabled = false
        status.text = activity.getString(R.string.status_capturing)
        ui.removeCallbacks(captureWatchdog)
        ui.postDelayed(captureWatchdog, CAPTURE_TIMEOUT_MS) // a still that never arrives must not freeze Shoot
        ic.targetRotation = displayRotation() // the activity is not recreated on rotation, so set it per shot
        val out = ByteArrayOutputStream(512 * 1024)
        val wanted = ic
        ic.takePicture(ImageCapture.OutputFileOptions.Builder(out).build(), activity.mainExecutor, object : ImageCapture.OnImageSavedCallback {
            override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                if (imageCapture !== wanted) return // the camera was closed while the shot was in flight
                onJpeg(out.toByteArray())
            }

            override fun onError(exception: ImageCaptureException) {
                if (imageCapture !== wanted) return
                captureFailed(exception.imageCaptureError)
            }
        })
    }

    private fun captureFailed(code: Int) {
        ui.removeCallbacks(captureWatchdog)
        capturing = false
        shoot.isEnabled = camera != null && !awaitingPage
        status.text = activity.getString(R.string.camera_error, code)
        if (autoToggle.isChecked) autoToggle.isChecked = false
    }

    /** The JPEG CameraX wrote: upright through its EXIF orientation, which the page's image decoder honours. */
    private fun onJpeg(bytes: ByteArray) {
        if (!capturing) return // the watchdog already gave up on this shot, or the camera was closed
        ui.removeCallbacks(captureWatchdog)
        capturing = false
        if (closed || camera == null || awaitingPage) return // a late shot after a round trip was given up on
        awaitingPage = true
        expectedSeq = -1
        shoot.isEnabled = false
        status.text = activity.getString(R.string.status_reading)
        ui.removeCallbacks(watchdog)
        ui.postDelayed(watchdog, PAGE_TIMEOUT_MS)
        host.photo(bytes, guideFractions())
    }

    /**
     * The dashed box as fractions of the photo, or null when the viewfinder and the photo do not
     * share an aspect (then the page searches the whole picture). Both use cases ask for 4:3, so
     * they normally match; the check guards the fallback cases.
     */
    private fun guideFractions(): FloatArray? {
        val pr = preview?.resolutionInfo ?: return null
        val cr = imageCapture?.resolutionInfo ?: return null
        // Both are compared in the photo's orientation; the preview's own rotation is the display's.
        val pu = TorchMath.uprightSize(pr.resolution.width, pr.resolution.height, cr.rotationDegrees)
        val cu = TorchMath.uprightSize(cr.resolution.width, cr.resolution.height, cr.rotationDegrees)
        val pa = pu[0].toFloat() / pu[1]
        val ca = cu[0].toFloat() / cu[1]
        if (Math.abs(pa - ca) > 0.02f) return null
        val g = guideView.guide()
        return TorchMath.guideFractions(previewView.width, previewView.height, pu[0], pu[1], g.left, g.top, g.width(), g.height())
    }

    // -- what the page did with the photo -------------------------------------

    /** The page took the photo and gave it this sequence number; events for other numbers are stale. */
    fun photoAccepted(seq: Int) {
        if (awaitingPage) expectedSeq = seq
    }

    /** The page could not take the photo at all (the glue is gone, or the page was left). */
    fun handoffFailed() {
        if (!awaitingPage) return
        pageDone(activity.getString(R.string.scan_handoff_failed), stopAuto = true)
    }

    private fun pageTimedOut() {
        pageDone(activity.getString(R.string.scan_page_slow), stopAuto = true)
    }

    private fun pageDone(line: String?, stopAuto: Boolean = false) {
        ui.removeCallbacks(watchdog)
        awaitingPage = false
        expectedSeq = -1
        shoot.isEnabled = camera != null
        if (line != null) status.text = line
        if (stopAuto && autoToggle.isChecked) autoToggle.isChecked = false
        if (autoToggle.isChecked && !closed) ui.postDelayed(nextAuto, AUTO_GAP_MS)
    }

    private fun showResult(text: String, undoable: Boolean) {
        result.text = text
        result.visibility = View.VISIBLE
        canUndo = undoable
        undo.isEnabled = undoable
        undo.visibility = if (undoable) View.VISIBLE else View.GONE
    }

    /** One JSON event from the page (see [ScanGlue]). Runs on the UI thread. */
    fun onScanEvent(json: String) {
        val ev = try {
            JSONObject(json)
        } catch (e: JSONException) {
            return
        }
        if (ev.optString("nonce") != nonce) return // not from the glue this panel installed
        val type = ev.optString("type")
        val completion = type == "done" || type == "no-card"
        if (completion) {
            // Only the photo in flight may end a round trip: a late answer to an earlier photo (one the
            // watchdog gave up on) must not free Shoot while another photo is with the page.
            if (!awaitingPage) return
            if (expectedSeq >= 0 && ev.optInt("seq", -1) != expectedSeq) return
        }
        val name = ev.optString("name", "")
        val total = if (ev.has("total") && !ev.isNull("total")) ev.optInt("total") else -1
        val cards = if (total >= 0) activity.resources.getQuantityString(R.plurals.cards, total, total) else ""
        val undoable = ev.optInt("undoable", 0) > 0
        when (type) {
            "auto-added" -> showResult(activity.getString(R.string.added_card, name, cards), undoable)
            "undo" -> showResult(activity.getString(R.string.removed_card, name, cards), undoable)
            "unsure" -> showResult(activity.getString(R.string.unsure_card, name), undoable)
            "unidentified" -> showResult(activity.getString(R.string.unidentified_card, name), undoable)
            "no-card" -> pageDone(activity.getString(R.string.no_card_outline))
            "done" -> {
                val error = ev.optString("error", "")
                val pageStatus = ev.optString("status", "")
                val pending = ev.optBoolean("pending", false)
                when {
                    error.isNotEmpty() -> pageDone(activity.getString(R.string.scan_failed, error), stopAuto = true)
                    // A read waits on the page for a tap. The page's own continuous mode stops shooting
                    // while one does, and shooting on would only replace it, so Auto pauses here too.
                    pending && autoToggle.isChecked -> pageDone(activity.getString(R.string.auto_paused_pending), stopAuto = true)
                    pending -> pageDone(activity.getString(R.string.pending_on_page))
                    pageStatus.isNotEmpty() -> pageDone(pageStatus)
                    else -> pageDone(activity.getString(R.string.status_next))
                }
            }
        }
    }

    // -- fold posture ----------------------------------------------------------------

    /** The hinge as WindowManager reports it for the activity's window, or null when flat or unknown. */
    fun onFold(feature: FoldingFeature?) {
        fold = feature
        applyPosture()
    }

    private fun applyPosture() {
        val f = fold
        val split = if (f == null || f.state != FoldingFeature.State.HALF_OPENED) null else {
            val loc = IntArray(2)
            view.getLocationInWindow(loc)
            val b = f.bounds
            FoldSplit.compute(
                view.width, view.height,
                b.left - loc[0], b.top - loc[1], b.right - loc[0], b.bottom - loc[1],
                horizontal = f.orientation == FoldingFeature.Orientation.HORIZONTAL,
            )
        }
        if (postureApplied && split == lastSplit) return
        postureApplied = true
        lastSplit = split
        val vf = viewfinder.layoutParams as FrameLayout.LayoutParams
        val ct = controls.layoutParams as FrameLayout.LayoutParams
        if (split == null) {
            set(vf, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.TOP or Gravity.LEFT, 0, 0)
            set(ct, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM, 0, 0)
            controls.setBackgroundResource(R.drawable.panel_bg)
        } else if (split.horizontal) {
            // Tabletop: viewfinder above the hinge, controls on the flat half below it.
            set(vf, ViewGroup.LayoutParams.MATCH_PARENT, split.first, Gravity.TOP or Gravity.LEFT, 0, 0)
            set(ct, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.TOP, 0, split.second)
            controls.setBackgroundColor(activity.getColor(R.color.cam_bg))
        } else {
            // Book: viewfinder on the left page, controls on the right one.
            set(vf, split.first, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.TOP or Gravity.LEFT, 0, 0)
            set(ct, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.TOP or Gravity.LEFT, split.second, 0)
            controls.setBackgroundColor(activity.getColor(R.color.cam_bg))
        }
        viewfinder.layoutParams = vf
        controls.layoutParams = ct
    }

    private fun set(lp: FrameLayout.LayoutParams, w: Int, h: Int, gravity: Int, left: Int, top: Int) {
        lp.width = w
        lp.height = h
        lp.gravity = gravity
        lp.leftMargin = left
        lp.topMargin = top
    }

    // -- geometry -------------------------------------------------------------

    private fun displayRotation(): Int =
        if (android.os.Build.VERSION.SDK_INT >= 30) activity.display?.rotation ?: Surface.ROTATION_0
        else @Suppress("DEPRECATION") activity.windowManager.defaultDisplay.rotation

    private fun onChange(block: (Int) -> Unit) = object : SeekBar.OnSeekBarChangeListener {
        override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
            if (fromUser) block(progress)
        }

        override fun onStartTrackingTouch(seekBar: SeekBar) {}
        override fun onStopTrackingTouch(seekBar: SeekBar) {}
    }

    companion object {
        private const val STRENGTH_STEPS = 20
        private const val ZOOM_STEPS = 40
        private const val MAX_JPEG_SIDE = 2048
        /** Pause between automatic shots once the page has answered, like the page's own frame gap. */
        private const val AUTO_GAP_MS = 600L
        /** OCR on a slow phone can take a while (the first read also loads the OCR engine); past this the photo counts as lost. */
        private const val PAGE_TIMEOUT_MS = 45_000L
        /** A still capture that neither delivers nor fails within this is given up on. */
        private const val CAPTURE_TIMEOUT_MS = 6_000L
        /** How long to wait for CameraX to report the device closed before telling the page anyway. */
        private const val CLOSE_TIMEOUT_MS = 2_000L
    }
}
