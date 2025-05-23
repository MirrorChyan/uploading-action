import os as ospkg
import sys
import time
import urllib3
import requests
from datetime import datetime

urllib3.disable_warnings()

_, rid, version, os, arch, channel, token, file = sys.argv

data = {
    "name": version,
    "os": os,
    "arch": arch,
    "channel": channel,
}

file_ext = ospkg.path.splitext(file)[1]
download_name = f"{"-".join(filter(lambda x: x != "", [rid, os, arch, version]))}{file_ext}"

if file_ext != ".zip":
    data["filename"] = download_name


headers = {
    "Authorization": token,
    "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded",
}


def log(msg: object) -> None:
    print(f"{datetime.now()} | {msg}")


def upload() -> bool:
    log("start upload")

    # step 1

    response_1 = requests.post(
        f"https://mirrorchyan.com/api/resources/{rid}/versions",
        headers=headers,
        data=data,
        verify=False,
    )
    log(f"step 1: {response_1.status_code}")

    if response_1.status_code != 200:
        log(f"step 1 failed: {response_1.status_code}, {response_1.text}")
        return False

    # step 2
    response_1_data = response_1.json()["data"]

    response_2 = requests.post(
        response_1_data["host"],
        data={
            "success_action_status": "200",
            "name": response_1_data["name"],
            "signature": response_1_data["signature"],
            "key": response_1_data["key"],
            "policy": response_1_data["policy"],
            "OSSAccessKeyId": response_1_data["access_key"],
            'Content-Disposition': f'attachment; filename="{download_name}"'
        },
        files={"file": open(file, "rb")},
        verify=False,
    )

    log(f"step 2: {response_2.status_code}")

    if response_2.status_code != 200:
        log(f"step 2 failed: {response_2.status_code}, {response_2.text}")
        return False

    # step 3
    data["key"] = response_1_data["key"]

    response_3 = requests.post(
        f"https://mirrorchyan.com/api/resources/{rid}/versions/callback",
        headers=headers,
        data=data,
        verify=False,
    )

    log(f"step 3: {response_3.status_code}")

    if response_3.status_code != 200:
        log(f"step 3 failed: {response_3.status_code}, {response_3.text}")
        return False

    log("uploaded")
    return True


if __name__ == "__main__":
    log(data)

    done = False
    retires = 3
    for i in range(retires):
        if upload():
            done = True
            break
        elif i == retires - 1:
            break
        else:
            sleep_time = 30
            log(f"retry {i + 1} after {sleep_time}s")
            time.sleep(sleep_time)

    if not done:
        log("failed")
        exit(1)

    log("done")
