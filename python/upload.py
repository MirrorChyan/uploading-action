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

headers = {
    "Authorization": token,
    "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded",
}
print(data)


def upload() -> bool:

    print(f"{datetime.now()} | start upload")

    # step 1

    response_1 = requests.post(
        f"https://mirrorchyan.com/api/resources/{rid}/versions",
        headers=headers,
        data=data,
        verify=False,
    )
    print(f"{datetime.now()} | step 1: {response_1.status_code}")

    if response_1.status_code != 200:
        print(f"step 1 failed: {response_1.status_code}, {response_1.text}")
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
        },
        files={"file": open(file, "rb")},
        verify=False,
    )

    print(f"{datetime.now()} | step 2: {response_2.status_code}")

    if response_2.status_code != 200:
        print(f"step 2 failed: {response_2.status_code}, {response_2.text}")
        return False

    # step 3
    data["key"] = response_1_data["key"]

    response_3 = requests.post(
        f"https://mirrorchyan.com/api/resources/{rid}/versions/callback",
        headers=headers,
        data=data,
        verify=False,
    )

    print(f"{datetime.now()} | step 3: {response_3.status_code}")

    if response_3.status_code != 200:
        print(f"step 3 failed: {response_3.status_code}, {response_3.text}")
        return False


if __name__ == "__main__":
    done = False
    for i in range(3):
        if upload():
            done = True
            break
        else:
            print(f"{datetime.now()} | retry {i + 1}")
            time.sleep(10)

    if not done:
        print(f"{datetime.now()} | failed")
        exit(1)

    print(f"{datetime.now()} | done")
