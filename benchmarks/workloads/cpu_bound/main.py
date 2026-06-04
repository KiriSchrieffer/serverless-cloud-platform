def handler(event, context):
    n = int(event.get("n", 100_000))
    total = 0
    for value in range(n):
        total += value * value
    return {"total": total}
