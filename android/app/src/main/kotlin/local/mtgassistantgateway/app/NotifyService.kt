package local.mtgassistantgateway.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/**
 * The member's notifications while the app is closed: a foreground service that keeps one
 * connection open to the gateway's own address ([Push.PATH]) and shows each message the gateway
 * relays. No Google push service, no other app. Android shows its quiet "Connected" notification
 * while it runs, as it must for a foreground service.
 *
 * It runs while the member has pressed Get notifications on this phone ([Prefs.pushKey] set), and
 * starts again after the phone restarts and after an app update ([Restart]) and whenever the app is
 * opened (after Stop in Android's task manager, say). It stops for good when the gateway says the
 * key is nobody's or notifications are off (the member switched them off, or pressed Disconnect my
 * phones), and when the app is set up for another gateway. A connection that fails is tried again
 * after [Push.backoffSeconds], at once when the phone's network comes back.
 */
class NotifyService : Service() {
    private lateinit var prefs: Prefs
    @Volatile private var running = false
    @Volatile private var connection: HttpURLConnection? = null
    private var worker: Thread? = null
    private val wake = Object() // the worker waits on it between tries; the network callback wakes it
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        channels(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val key = prefs.pushKey
        val origin = prefs.gatewayOrigin
        if (!Push.isKey(key) || origin == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(ONGOING_ID, ongoing(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(ONGOING_ID, ongoing())
            }
        } catch (e: RuntimeException) { // Android refused a start from the background
            Log.w(TAG, "not started: ${e.javaClass.simpleName}")
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) {
            running = true
            watchNetwork()
            worker = Thread({ loop(origin, key!!) }, "notifications").also { it.start() }
        }
        return START_STICKY // Android starts it again if it had to stop it for memory
    }

    override fun onDestroy() {
        running = false
        unwatchNetwork()
        connection?.disconnect()
        synchronized(wake) { wake.notifyAll() }
        worker?.interrupt()
        super.onDestroy()
    }

    /** Connect, read until the connection ends, wait, again; until stopped or refused for good. */
    private fun loop(origin: String, key: String) {
        var failures = 0
        while (running) {
            val started = SystemClock.elapsedRealtime()
            var outcome = try {
                listen(origin, key)
            } catch (e: IOException) {
                Log.i(TAG, "connection ended: ${e.javaClass.simpleName}")
                Push.Refusal.RETRY
            }
            // One that worked but ended at once (a proxy closing it, say) is no better than a failure.
            if (outcome == Push.Refusal.NONE && SystemClock.elapsedRealtime() - started < Push.WORKED_MS) {
                outcome = Push.Refusal.RETRY
            }
            if (!running) return
            when (outcome) {
                Push.Refusal.STOP -> {
                    Log.i(TAG, "the gateway refused this phone's key; notifications stop")
                    prefs.pushKey = null
                    stopSelf()
                    return
                }
                Push.Refusal.NONE -> failures = 0 // it worked, then ended: try again soon
                Push.Refusal.RETRY -> failures++
            }
            val waitS = if (failures == 0) 1L else Push.backoffSeconds(failures, Random.nextDouble())
            synchronized(wake) {
                try {
                    wake.wait(TimeUnit.SECONDS.toMillis(waitS))
                } catch (e: InterruptedException) {
                    return
                }
            }
        }
    }

    /** One connection: NONE once it worked and ended, else what the gateway's answer means. */
    private fun listen(origin: String, key: String): Push.Refusal {
        val since = Push.since(prefs.pushLastId)
        val url = GatewayUrl.join(origin, Push.PATH) +
            (if (since != null) "?since=" + URLEncoder.encode(since, "UTF-8") else "")
        val c = URL(url).openConnection() as HttpURLConnection
        connection = c
        try {
            c.connectTimeout = 20_000
            c.readTimeout = Push.READ_TIMEOUT_MS
            c.instanceFollowRedirects = false
            c.useCaches = false
            c.setRequestProperty("Authorization", "Bearer $key")
            c.setRequestProperty("Accept", "application/x-ndjson")
            c.setRequestProperty("User-Agent", "MTGAssistant/" + BuildConfig.VERSION_NAME)
            val status = c.responseCode
            if (status != 200) {
                val body = try {
                    c.errorStream?.use { it.readBytes().take(2048).toByteArray().toString(Charsets.UTF_8) }
                } catch (e: IOException) {
                    null
                }
                return Push.refusal(status, body)
            }
            val seen = Push.Seen(prefs.pushSeen)
            BufferedReader(InputStreamReader(c.inputStream, Charsets.UTF_8)).use { r ->
                while (running) {
                    val line = r.readLine() ?: break
                    handle(Push.parse(line), seen)
                }
            }
            return Push.Refusal.NONE
        } finally {
            connection = null
            c.disconnect()
        }
    }

    private fun handle(action: Push.Action, seen: Push.Seen) {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        when (action) {
            is Push.Action.Show -> {
                prefs.pushLastId = action.id
                if (!seen.first(action.id)) return
                prefs.pushSeen = seen.stored()
                // A short hold so the CPU stays up while the notification is posted (Doze included).
                val wl = getSystemService(PowerManager::class.java)
                    ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mtgassistant:notify")
                wl?.acquire(10_000)
                try {
                    if (canPost(this)) nm.notify(action.tag, MESSAGE_ID, message(action))
                } finally {
                    if (wl?.isHeld == true) wl.release()
                }
            }
            is Push.Action.Remove -> nm.cancel(action.tag, MESSAGE_ID)
            Push.Action.Alive, Push.Action.Skip -> Unit
        }
    }

    private fun message(m: Push.Action.Show): Notification {
        // A tap opens the message's page in the app, under the app's usual link rules (gateway pages only).
        val open = Intent(this, MainActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            data = Uri.parse(m.click ?: (prefs.gatewayOrigin + "/"))
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val tap = PendingIntent.getActivity(
            this, m.tag.hashCode(), open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_MESSAGES)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(m.title)
            .setContentText(m.text)
            .setStyle(Notification.BigTextStyle().bigText(m.text))
            .setContentIntent(tap)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_MESSAGE)
            .build()
    }

    private fun ongoing(): Notification {
        val open = Intent(this, MainActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            data = Uri.parse(prefs.gatewayOrigin + "/account#notifications")
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val tap = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return Notification.Builder(this, CHANNEL_CONNECTION)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(getString(R.string.notify_connected))
            .setContentText(getString(R.string.notify_connected_text))
            .setContentIntent(tap)
            .setOngoing(true)
            .setShowWhen(false)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    /** The network came back: try again now instead of at the end of the wait. */
    private fun watchNetwork() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                synchronized(wake) { wake.notifyAll() }
            }
        }
        try {
            cm.registerDefaultNetworkCallback(cb)
            networkCallback = cb
        } catch (e: RuntimeException) {
            Log.w(TAG, "network callback not registered", e)
        }
    }

    private fun unwatchNetwork() {
        val cb = networkCallback ?: return
        networkCallback = null
        try {
            getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(cb)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "network callback already gone", e)
        }
    }

    /** Starts the service again after the phone restarted or the app was updated, when it was on. */
    class Restart : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
            start(context)
        }
    }

    companion object {
        private const val TAG = "MtgNotify"
        private const val ONGOING_ID = 1
        private const val MESSAGE_ID = 2
        const val CHANNEL_MESSAGES = "messages"
        const val CHANNEL_CONNECTION = "connection"

        /** The channels: the members' messages (normal), and the quiet "Connected" notification. */
        fun channels(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_MESSAGES, context.getString(R.string.notify_channel_messages), NotificationManager.IMPORTANCE_DEFAULT),
            )
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_CONNECTION, context.getString(R.string.notify_channel_connection), NotificationManager.IMPORTANCE_MIN).apply {
                    description = context.getString(R.string.notify_channel_connection_text)
                    setShowBadge(false)
                },
            )
        }

        /** True when Android lets the app post the members' messages. */
        fun canPost(context: Context): Boolean {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) return false
            val nm = context.getSystemService(NotificationManager::class.java) ?: return false
            if (!nm.areNotificationsEnabled()) return false
            return nm.getNotificationChannel(CHANNEL_MESSAGES)?.importance != NotificationManager.IMPORTANCE_NONE
        }

        /** Starts the service when this phone was connected; a no-op otherwise. */
        fun start(context: Context) {
            val prefs = Prefs(context)
            if (!Push.isKey(prefs.pushKey) || prefs.gatewayOrigin == null) return
            try {
                context.startForegroundService(Intent(context, NotifyService::class.java))
            } catch (e: RuntimeException) { // not allowed from the background right now
                Log.w(TAG, "not started: ${e.javaClass.simpleName}")
            }
        }

        /** Stops the service and forgets the key (another gateway, or the member turned it off). */
        fun stop(context: Context) {
            Prefs(context).pushKey = null
            context.stopService(Intent(context, NotifyService::class.java))
        }

    }
}
