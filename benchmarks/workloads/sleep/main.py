import time


def handler(event, context):
    seconds = float(event.get("seconds", 1))
    time.sleep(seconds)
    return {"slept_seconds": seconds}
