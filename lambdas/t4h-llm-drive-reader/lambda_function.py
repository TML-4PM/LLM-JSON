
"""
t4h-llm-drive-reader
Reads Google Drive files via service account JWT auth (stdlib only).
Writes asset metadata to Supabase asset registry.
"""
import json, os, time, math, base64, hmac, hashlib, urllib.request, urllib.parse
from datetime import datetime, timezone

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SA_JSON      = json.loads(os.environ["GOOGLE_SA_JSON"])

SCOPES = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_API = "https://www.googleapis.com/drive/v3"

def _b64url(data):
    if isinstance(data, str): data = data.encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _sign_rsa(message, private_key_pem):
    """Sign with RS256 using RSA private key — using cryptography if available, else fallback."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        sig = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return _b64url(sig)
    except ImportError:
        raise RuntimeError("cryptography package required for RSA signing")

def get_access_token():
    """Get OAuth2 access token via JWT service account flow."""
    now = int(time.time())
    header  = _b64url(json.dumps({"alg":"RS256","typ":"JWT"}))
    payload = _b64url(json.dumps({
        "iss": SA_JSON["client_email"],
        "scope": SCOPES,
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now
    }))
    sig = _sign_rsa(f"{header}.{payload}", SA_JSON["private_key"])
    jwt = f"{header}.{payload}.{sig}"

    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
        headers={"Content-Type":"application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    return resp["access_token"]

def drive_list(token, query="", page_token=None, max_results=100):
    params = {
        "pageSize": min(max_results, 1000),
        "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink)",
        "q": query or "trashed=false",
    }
    if page_token: params["pageToken"] = page_token
    url = f"{DRIVE_API}/files?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization":f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def sb_upsert(table, rows):
    data = json.dumps(rows).encode()
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{table}", data=data,
        headers={"apikey":SUPABASE_KEY,"Authorization":f"Bearer {SUPABASE_KEY}",
                 "Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"})
    req.get_method = lambda: "POST"
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status

def handler(event, context):
    action     = event.get("action","list")
    max_files  = int(event.get("max_files", 200))
    query      = event.get("query","trashed=false")
    write_db   = event.get("write_db", True)

    print(json.dumps({"step":"start","action":action,"max_files":max_files}))

    try:
        token = get_access_token()
        print(json.dumps({"step":"auth","ok":True}))
    except Exception as e:
        return {"statusCode":500,"body":json.dumps({"error":f"Auth failed: {e}"})}

    files = []
    page_token = None
    while len(files) < max_files:
        batch_size = min(max_files - len(files), 1000)
        result = drive_list(token, query, page_token, batch_size)
        files.extend(result.get("files",[]))
        page_token = result.get("nextPageToken")
        if not page_token: break

    print(json.dumps({"step":"listed","count":len(files)}))

    if not write_db or not files:
        return {"statusCode":200,"body":json.dumps({"ok":True,"files":len(files),"sample":[f["name"] for f in files[:5]]})}

    # Write to Supabase drive_assets table
    rows = []
    for f in files:
        rows.append({
            "drive_file_id":  f["id"],
            "name":           f["name"],
            "mime_type":      f.get("mimeType"),
            "size_bytes":     int(f.get("size",0)) if f.get("size") else None,
            "created_time":   f.get("createdTime"),
            "modified_time":  f.get("modifiedTime"),
            "web_view_link":  f.get("webViewLink"),
            "parent_ids":     f.get("parents",[]),
            "indexed_at":     datetime.now(timezone.utc).isoformat(),
            "sa_email":       SA_JSON["client_email"],
        })

    # Upsert in batches of 100
    written = 0
    for i in range(0, len(rows), 100):
        batch = rows[i:i+100]
        try:
            sb_upsert("drive_assets", batch)
            written += len(batch)
        except Exception as e:
            print(json.dumps({"step":"write_error","batch":i,"error":str(e)[:100]}))

    print(json.dumps({"step":"done","listed":len(files),"written":written}))
    return {"statusCode":200,"body":json.dumps({"ok":True,"listed":len(files),"written":written,"sa":SA_JSON["client_email"]})}
