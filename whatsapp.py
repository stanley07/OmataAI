"""Inbound text-only pilot. Run separately; never expose the operator dashboard."""
import hashlib, hmac, json, os, sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs
import app
import whatsapp_outbound
from whatsapp_responses import format_response, WELCOME

def load_routes():
    routes=json.loads(os.environ.get('WHATSAPP_ROUTES','[]'))
    if not isinstance(routes,list) or not routes:raise ValueError('Set WHATSAPP_ROUTES to a non-empty JSON list.')
    mapping={}
    with app.connect() as c:
        for r in routes:
            if not isinstance(r,dict):raise ValueError('Each route must be an object.')
            sender=r.get('sender');receiver=r.get('phone_number_id');shop=r.get('shop')
            if not all(isinstance(x,str) and x for x in (sender,receiver,shop)):raise ValueError('Routes need sender, phone_number_id and shop strings.')
            if not sender.isascii() or not sender.isdigit() or not receiver.isascii() or not receiver.isdigit():raise ValueError('Use digits only for sender and phone_number_id.')
            if (receiver,sender) in mapping:raise ValueError('Duplicate sender/receiver route.')
            if not c.execute('SELECT 1 FROM shops WHERE id=?',(shop,)).fetchone():raise ValueError('A route names an unknown shop. Create the shop first.')
            mapping[(receiver,sender)]=shop
    return mapping

def ingest(payload, routes):
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        raise ValueError("Expected a WhatsApp webhook object.")

    notes = []
    audio_jobs = []
    ignored = 0

    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue

                value = change["value"]
                receiver = value.get("metadata", {}).get("phone_number_id")

                for message in value.get("messages", []):
                    sender = message.get("from")
                    merchant = routes.get((receiver, sender))
                    ident = message.get("id")

                    if (
                        not merchant
                        or not isinstance(ident, str)
                        or not ident
                        or len(ident) > 500
                    ):
                        ignored += 1
                        continue

                    provider_key = "wa:" + receiver + ":" + ident
                    kind = message.get("type")

                    if kind == "text":
                        body = message.get("text", {}).get("body")

                        if (
                            not isinstance(body, str)
                            or not body.strip()
                            or len(body) > 2000
                        ):
                            ignored += 1
                            continue

                        notes.append((
                            merchant, provider_key,
                            sender, receiver, body
                        ))

                    elif kind == "audio":
                        audio = message.get("audio")

                        if not isinstance(audio, dict):
                            ignored += 1
                            continue

                        media_id = audio.get("id")
                        mime_type = audio.get("mime_type") or ""

                        if (
                            not isinstance(media_id, str)
                            or not media_id
                            or len(media_id) > 2000
                            or not isinstance(mime_type, str)
                        ):
                            ignored += 1
                            continue

                        audio_jobs.append((
                            merchant, provider_key,
                            sender, receiver, media_id, mime_type
                        ))

                    else:
                        ignored += 1

    except (AttributeError, TypeError, KeyError):
        raise ValueError("Malformed webhook payload.")

    for note in notes:
        inbox_id = app.inbox_add(*note)

        try:
            review = app.inbox_parse(inbox_id)
            reply_text = format_response(review)
        except sqlite3.Error:
            raise
        except Exception as exc:
            print(
                "WhatsApp review parser fallback:",
                type(exc).__name__,
                flush=True
            )
            reply_text = WELCOME

        whatsapp_outbound.enqueue(
            note[1], note[2], note[3], reply_text
        )

    for audio_job in audio_jobs:
        job_id, created = app.voice_job_add(*audio_job)

        if created:
            app.start_voice_job(job_id)

    return {
        "accepted": len(notes) + len(audio_jobs),
        "ignored": ignored
    }

class WebhookHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(10)
    def reply(self,status,body):
        raw=body.encode();self.send_response(status);self.send_header('Content-Type','text/plain; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        url=urlsplit(self.path)
        if url.path!='/webhook':return self.reply(404,'Not found')
        q=parse_qs(url.query)
        token=q.get('hub.verify_token',[''])[0]
        if q.get('hub.mode')!=['subscribe'] or not hmac.compare_digest(token.encode(),self.server.verify_token.encode()):return self.reply(403,'Forbidden')
        challenge=q.get('hub.challenge',[''])[0]
        if not challenge or len(challenge)>1000:return self.reply(400,'Invalid challenge')
        self.reply(200,challenge)
    def do_POST(self):
        if self.path!='/webhook':return self.reply(404,'Not found')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1024*1024:return self.reply(413,'Invalid size')
            raw=self.rfile.read(size)
            expected='sha256='+hmac.new(self.server.app_secret.encode(),raw,hashlib.sha256).hexdigest()
            signature=self.headers.get('X-Hub-Signature-256','')
            if not hmac.compare_digest(expected.encode(),signature.encode()):return self.reply(403,'Invalid signature')
            result=ingest(json.loads(raw),self.server.routes)
            self.reply(200,json.dumps(result))
        except (ValueError,TypeError,UnicodeError):self.reply(400,'Invalid payload')
        except sqlite3.Error:self.reply(503,'Temporary database error; retry later')
    def log_message(self,*args):pass

def main():
    secret=os.environ.get('META_APP_SECRET','');verify=os.environ.get('WHATSAPP_VERIFY_TOKEN','')
    if not secret or not verify:raise SystemExit('Set META_APP_SECRET and WHATSAPP_VERIFY_TOKEN before starting the receiver.')
    try:routes=load_routes()
    except ValueError as e:raise SystemExit(str(e))
    server=HTTPServer(('127.0.0.1',8081),WebhookHandler)
    server.app_secret=secret;server.verify_token=verify;server.routes=routes
    print('Inbound text receiver: http://127.0.0.1:8081/webhook',flush=True)
    print('Not publicly reachable yet. Forward only port 8081 via HTTPS, never dashboard port 8080.',flush=True)
    whatsapp_outbound.start()
    app.recover_voice_acknowledgements()
    app.resume_pending_voice_jobs()
    server.serve_forever()

if __name__=='__main__':main()
