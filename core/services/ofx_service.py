from ofx_import import import_ofx_bytes

def handle_ofx_import(user_id: str, attachment_bytes: bytes, filename: str) -> dict:
    # converte user_id pra int se seu DB usa bigint
    uid = int(user_id)
    return import_ofx_bytes(uid, attachment_bytes, filename=filename)