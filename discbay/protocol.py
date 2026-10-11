"""The contract between the DiscBay daemon and every frontend.

This file is the "single source of truth". Frontends written in any language
only need what is described here (and in docs/PROTOCOL.md):

  * a D-Bus service on the *session* bus,
  * one signal, ``StateChanged(s json)``, carrying a full state snapshot,
  * a handful of methods to act on it.

Bump PROTOCOL_VERSION whenever a field is renamed or removed. Adding new
fields is backwards compatible and does not need a bump; frontends must
ignore fields they do not know.
"""

PROTOCOL_VERSION = 1

BUS_NAME = "me.jolen.DiscBay1"
OBJECT_PATH = "/me/jolen/DiscBay1"
INTERFACE = "me.jolen.DiscBay1"

# --- States: where the drive/disc is right now --------------------------------
EMPTY = "empty"            # no disc in the drive
SPINNING = "spinning"      # disc detected, drive is spinning up, data not readable yet
COUNTDOWN = "countdown"    # known disc identified, autostart timer running
UNKNOWN = "unknown"        # disc readable but not registered, and carries no manifest
UNTRUSTED = "untrusted"    # disc's own manifest wants to launch something; waiting for Trust()
IDLE = "idle"              # disc present, nothing pending (cancelled, or app exited)
RUNNING = "running"        # the disc's application was launched
ERROR = "error"            # disc unreadable or launch failed (see "message")

STATES = (EMPTY, SPINNING, COUNTDOWN, UNKNOWN, UNTRUSTED, IDLE, RUNNING, ERROR)

# --- Events: what just happened (use these for sounds and animations) -------------
EV_INSERTED = "inserted"       # disc physically detected -> play the insert beep here
EV_IDENTIFIED = "identified"   # label read
EV_CANCELLED = "cancelled"     # user cancelled the autostart, or declined to trust a disc
EV_LAUNCHED = "launched"       # app started
EV_APP_EXITED = "app_exited"   # app process ended
EV_EJECTED = "ejected"         # disc removed
EV_FAILED = "failed"           # something went wrong
EV_BUSY = "busy"               # "held" or "burning" changed (a tool like DiscBay Studio is working)
EV_SYNC = "sync"               # snapshot sent on request (GetState), nothing happened

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="GetState">
      <arg type="s" name="state_json" direction="out"/>
    </method>
    <method name="Cancel"/>
    <method name="Trust"/>
    <method name="LaunchNow"/>
    <method name="Eject"/>
    <method name="ReloadConfig"/>
    <method name="Hold">
      <arg type="u" name="seconds" direction="in"/>
      <arg type="b" name="exclusive" direction="in"/>
    </method>
    <method name="SimulateInsert">
      <arg type="s" name="label" direction="in"/>
      <arg type="s" name="manifest_toml" direction="in"/>
      <arg type="s" name="base_dir" direction="in"/>
    </method>
    <method name="SimulateRemove"/>
    <signal name="StateChanged">
      <arg type="s" name="state_json"/>
    </signal>
    <property name="ProtocolVersion" type="u" access="read"/>
  </interface>
</node>
"""
