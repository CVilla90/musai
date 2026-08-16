"""The Messaging Hub: compose once, send to a group's participants.

`store.py` owns the record (and therefore the idempotency guard, since Moodle offers no
marker that makes a re-send safe); `jobs.py` runs the browser in the background. The browser
itself lives in `musai/automation/messaging.py`, because the rule that the cloud app never
drives a browser applies here more than anywhere else.
"""
