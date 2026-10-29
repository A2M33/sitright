"""Auto-pause detection, fed with fake ConsentStore registry snapshots."""

import os

from sitright.inuse import apps_in_use, own_executables

OWN = r"C:\Users\me\AppData\Local\Programs\Python\Python312\pythonw.exe"

WEBCAM = {
    "Value": "Allow",
    # Store app holding the camera right now.
    "Microsoft.WindowsCamera_8wekyb3d8bbwe": {
        "LastUsedTimeStart": 134366348379815095,
        "LastUsedTimeStop": 0,
    },
    # Store app that used it earlier and let go.
    "Microsoft.SkypeApp_kzf8qxf38zg5c": {
        "LastUsedTimeStart": 134366348379815095,
        "LastUsedTimeStop": 134366348479815095,
    },
    # Store app that never used it: no values at all.
    "Microsoft.GamingApp_8wekyb3d8bbwe": {},
    "NonPackaged": {
        r"C:#Program Files#Zoom#bin#Zoom.exe": {
            "LastUsedTimeStart": 134376849025657923,
            "LastUsedTimeStop": 0,
        },
        r"C:#Program Files#Google#Chrome#Application#chrome.exe": {
            "LastUsedTimeStart": 134365573171679089,
            "LastUsedTimeStop": 134365573271679089,
        },
        # SitRight itself while it watches you.
        OWN.replace("\\", "#"): {"LastUsedTimeStart": 134376849025657923, "LastUsedTimeStop": 0},
    },
}


def test_reports_apps_holding_the_device():
    assert apps_in_use(WEBCAM, own_exes=[OWN]) == ["Microsoft.WindowsCamera", "Zoom.exe"]


def test_excludes_our_own_process_case_insensitively():
    assert "pythonw.exe" not in apps_in_use(WEBCAM, own_exes=[OWN.upper()])
    assert "pythonw.exe" in apps_in_use(WEBCAM, own_exes=[])


def test_ignore_list():
    assert apps_in_use(WEBCAM, [OWN], ignore=["zoom.exe", "Microsoft.WindowsCamera_8wekyb3d8bbwe"]) == []


def test_nothing_in_use():
    idle = {
        "NonPackaged": {
            r"C:#app.exe": {"LastUsedTimeStart": 5, "LastUsedTimeStop": 9},
        },
        "Some.App_x": {"LastUsedTimeStart": 5, "LastUsedTimeStop": 9},
    }
    assert apps_in_use(idle) == []
    assert apps_in_use({}) == []


def test_own_executables_cover_console_and_windowed_python():
    names = {os.path.basename(p).lower() for p in own_executables()}
    assert {"python.exe", "pythonw.exe"} <= names
