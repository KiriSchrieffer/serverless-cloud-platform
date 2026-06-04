def handler(event, context):
    size_mb = int(event.get("size_mb", 512))
    data = bytearray(size_mb * 1024 * 1024)
    return {"allocated_mb": len(data) // 1024 // 1024}
