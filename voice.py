"""Local ASR and conservative explicit-field extraction. No ledger writes."""
import os, re, tempfile
from decimal import Decimal
from pathlib import Path

MAX_AUDIO_BYTES = 10 * 1024 * 1024
_model = None


def _audio_metadata(raw, mime_type=''):
    mime = str(mime_type or '').split(';',1)[0].strip().lower()
    mapping = {
        'audio/ogg': ('voice-note.ogg','audio/ogg'),
        'audio/opus': ('voice-note.ogg','audio/ogg'),
        'audio/mpeg': ('voice-note.mp3','audio/mpeg'),
        'audio/mp3': ('voice-note.mp3','audio/mpeg'),
        'audio/mp4': ('voice-note.m4a','audio/mp4'),
        'audio/m4a': ('voice-note.m4a','audio/mp4'),
        'audio/x-m4a': ('voice-note.m4a','audio/mp4'),
        'audio/wav': ('voice-note.wav','audio/wav'),
        'audio/x-wav': ('voice-note.wav','audio/wav'),
        'audio/webm': ('voice-note.webm','audio/webm'),
    }
    if mime in mapping:
        return mapping[mime]
    if raw.startswith(b'OggS'):
        return 'voice-note.ogg','audio/ogg'
    if raw.startswith(b'RIFF'):
        return 'voice-note.wav','audio/wav'
    if raw[:3] == b'ID3' or raw[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
        return 'voice-note.mp3','audio/mpeg'
    if len(raw) >= 12 and raw[4:8] == b'ftyp':
        return 'voice-note.m4a','audio/mp4'
    if raw.startswith(b'\x1aE\xdf\xa3'):
        return 'voice-note.webm','audio/webm'
    return 'voice-note.ogg','audio/ogg'


def transcribe(raw, mime_type='', prompt='', keywords=None, languages=None):
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise ValueError('Choose an audio file between 1 byte and 10 MB.')

    api_key = os.environ.get('OPENAI_API_KEY','').strip()
    if not api_key:
        raise ValueError('OPENAI_API_KEY is not configured for speech transcription.')

    import json
    import uuid
    import urllib.error
    import urllib.request

    filename, content_type = _audio_metadata(raw, mime_type)
    boundary = '----VoiceLedger' + uuid.uuid4().hex

    def field(name, value):
        return (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f'{value}\r\n'
        ).encode('utf-8')

    body = bytearray()
    body += field('model', os.environ.get('VOICE_TRANSCRIBE_MODEL','gpt-transcribe'))

    prompt = str(prompt or '').strip()
    if prompt:
        body += field('prompt', prompt[:1200])

    cleaned_keywords = []
    for item in (keywords or []):
        value = ' '.join(str(item or '').replace('\r',' ').replace('\n',' ').split()).strip()
        if not value or '<' in value or '>' in value:
            continue
        if value.casefold() not in {x.casefold() for x in cleaned_keywords}:
            cleaned_keywords.append(value[:100])
        if len(cleaned_keywords) >= 60:
            break
    for value in cleaned_keywords:
        body += field('keywords[]', value)

    cleaned_languages = []
    for item in (languages or []):
        value = str(item or '').strip().lower()
        if re.fullmatch(r'[a-z]{2}', value) and value not in cleaned_languages:
            cleaned_languages.append(value)
    for value in cleaned_languages[:4]:
        body += field('languages[]', value)

    body += (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'
    ).encode('utf-8')
    body += raw
    body += b'\r\n'
    body += f'--{boundary}--\r\n'.encode('utf-8')

    req = urllib.request.Request(
        'https://api.openai.com/v1/audio/transcriptions',
        data=bytes(body),
        method='POST',
        headers={
            'Authorization': 'Bearer ' + api_key,
            'Content-Type': 'multipart/form-data; boundary=' + boundary,
            'Accept': 'application/json',
            'User-Agent': 'Voice-Ledger/1.0',
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        try:
            message = json.loads(detail).get('error',{}).get('message') or detail
        except Exception:
            message = detail

        # Some transcription model/API combinations may reject optional hint fields.
        # If that happens, retry once with the broadly-supported prompt only rather
        # than dropping the voice note entirely.
        lower = str(message).lower()
        if error.code == 400 and cleaned_keywords and (
            'keyword' in lower or 'language' in lower or 'unknown' in lower or 'unsupported' in lower
        ):
            body2 = bytearray()
            body2 += field('model', os.environ.get('VOICE_TRANSCRIBE_MODEL','gpt-transcribe'))
            if prompt:
                body2 += field('prompt', prompt[:1200])
            body2 += (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f'Content-Type: {content_type}\r\n\r\n'
            ).encode('utf-8')
            body2 += raw
            body2 += b'\r\n'
            body2 += f'--{boundary}--\r\n'.encode('utf-8')
            req2 = urllib.request.Request(
                'https://api.openai.com/v1/audio/transcriptions',
                data=bytes(body2),
                method='POST',
                headers={
                    'Authorization': 'Bearer ' + api_key,
                    'Content-Type': 'multipart/form-data; boundary=' + boundary,
                    'Accept': 'application/json',
                    'User-Agent': 'Voice-Ledger/1.0',
                },
            )
            try:
                with urllib.request.urlopen(req2, timeout=120) as response:
                    payload = json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as retry_error:
                retry_detail = retry_error.read().decode('utf-8', errors='replace')
                try:
                    retry_message = json.loads(retry_detail).get('error',{}).get('message') or retry_detail
                except Exception:
                    retry_message = retry_detail
                raise ValueError(f'OpenAI transcription failed (HTTP {retry_error.code}): {retry_message[:300]}') from retry_error
        elif error.code == 401:
            raise ValueError('OpenAI transcription authentication failed. Check OPENAI_API_KEY.') from error
        elif error.code == 429:
            raise ValueError('OpenAI transcription is rate-limited or the API account has no available quota. Check API billing/quota and retry.') from error
        else:
            raise ValueError(f'OpenAI transcription failed (HTTP {error.code}): {message[:300]}') from error
    except urllib.error.URLError as error:
        raise ValueError('Could not reach the OpenAI transcription API. Check the internet connection and retry.') from error
    except TimeoutError as error:
        raise ValueError('OpenAI transcription timed out. Retry with a shorter voice note.') from error

    text = str(payload.get('text') or '').strip()
    if not text:
        raise ValueError('No speech detected. Record again or type the transcript.')

    return {
        'text': text,
        'provider': 'OpenAI Audio API',
        'model': os.environ.get('VOICE_TRANSCRIBE_MODEL','gpt-transcribe'),
        'warning': 'Review every amount, quantity, product and customer before saving. Voice transcription can make mistakes.'
    }


ONES = dict(zip('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(1,20)))
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20,100,10)))
WORDS = set(ONES) | set(TENS) | {'zero','hundred','thousand','million','and'}
NUM = r"(?:[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?(?:\s*(?:k|thousand|million))?|(?:" + '|'.join(sorted(WORDS,key=len,reverse=True)) + r")(?:[ -]+(?:" + '|'.join(sorted(WORDS,key=len,reverse=True)) + r"))*)"


def number(raw):
    raw = raw.strip().lower()
    if re.fullmatch(r'\d+(?:,\d{3})*(?:\.\d+)?\s*(?:k|thousand|million)?', raw):
        m = re.fullmatch(r'([\d,.]+)\s*(k|thousand|million)?', raw)
        value = Decimal(m[1].replace(',','')) * {'k':1000,'thousand':1000,'million':1000000,None:1}[m[2]]
    else:
        tokens = raw.replace('-',' ').split()
        def small(ts):
            if not ts:return None
            if 'hundred' in ts:
                if len(ts)<2 or ts[1]!='hundred' or ts[0] not in ONES or ONES[ts[0]]>9:return None
                base=ONES[ts[0]]*100;tail=ts[2:]
                if not tail:return base
                if tail[0]=='and':tail=tail[1:]
                n=small(tail)
                return base+n if n is not None and 0<n<100 else None
            if len(ts)==1:return 0 if ts[0]=='zero' else ONES.get(ts[0],TENS.get(ts[0]))
            if len(ts)==2 and ts[0] in TENS and ts[1] in ONES and ONES[ts[1]]<10:return TENS[ts[0]]+ONES[ts[1]]
            return None
        total=0;last=1000000000;start=0
        for i,t in enumerate(tokens):
            if t not in ('million','thousand'):continue
            scale={'million':1000000,'thousand':1000}[t];n=small(tokens[start:i])
            if scale>=last or n is None or n<=0:return None
            total+=n*scale;last=scale;start=i+1
        tail=tokens[start:]
        if start and tail and tail[0]=='and':tail=tail[1:]
        n=small(tail) if tail else (0 if start else None)
        if n is None or (start and n>=last):return None
        value=Decimal(total+n)
    if value<0 or value>1000000000 or value*100!=(value*100).to_integral_value():return None
    return format(value,'f')


def _unique_amount_after_pattern(lowered, pattern):
    hits = list(re.finditer(pattern, lowered))
    if len(hits) != 1:return None
    tail = lowered[hits[0].end():]
    m = re.match(r'\s*(?:₦|ngn\s*)?(' + NUM + r')(?![\w,])', tail)
    if not m:return None
    rest = tail[m.end():]
    if re.match(r'\s+(?:\d|point\b|and\b|hundred\b|thousand\b|million\b)', rest):return None
    return number(m[1])


def _sale_item_suggestion(lowered):
    unit_pattern = r'(bags?|crates?|cartons?|pieces?|bottles?)'
    pattern = r'\b(?:sale|sell|sold|buy|buys|buying|bought|purchase|purchases|purchasing|purchased)\s+(' + NUM + r')\s+' + unit_pattern + r'\s+of\s+([a-z][a-z -]{0,60}?)(?=\s+(?:to|for|at|total|paid|received|cash)\b|[.,;]|$)'
    matches = list(re.finditer(pattern, lowered))
    if len(matches) != 1:return None,None,None
    q = number(matches[0][1])
    if q is None or Decimal(q)<=0 or Decimal(q)!=Decimal(q).to_integral_value():return None,None,None
    unit = matches[0][2].lower()
    product = ' '.join(matches[0][3].strip().split())
    return q,unit,product if product else (None,None,None)




def _stock_received_suggestion(text):
    """Conservatively extract one incoming-stock movement from common trade wording."""
    lowered=' '.join(str(text or '').strip().lower().split())
    unit_pattern=r'(bags?|crates?|cartons?|pieces?|bottles?|packs?|sacks?|boxes?|units?)'
    lead=r'(?:received|receive|restocked|restock|stocked|stock\s+received|delivered|supplied)'
    item_patterns=[
        r'\b'+lead+r'\s+('+NUM+r')\s+'+unit_pattern+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)(?=\s+(?:from|for|at|total|cost|worth)\b|[.,;]|$)',
        r'\b(?:bought|purchased)\s+(?:stock\s+of\s+)?('+NUM+r')\s+'+unit_pattern+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)(?=\s+(?:from|for|at|total|cost|worth)\b|[.,;]|$)',
    ]
    quantity_value=unit=product=None
    matches=[]
    for pattern in item_patterns:
        matches.extend(list(re.finditer(pattern,lowered,flags=re.IGNORECASE)))
    if len(matches)==1:
        m=matches[0]
        q=number(m[1])
        if q is not None and Decimal(q)>0:
            quantity_value=q
            unit=m[2].lower()
            product=' '.join(m[3].strip(" .,-").split())

    total_cost=_unique_amount_after_pattern(lowered,r'\b(?:for|cost(?:ing)?|worth|total(?:\s+cost)?)\b')
    if total_cost is None:
        total_cost=_unique_amount_after_pattern(lowered,r'\bat\b')

    supplier_name=None
    word=r"[A-Za-z][A-Za-z0-9'’&.-]*"
    supplier_patterns=[
        rf'\bfrom\s+({word}(?:\s+{word}){{0,4}}?)(?=\s+(?:for|at|total|cost|worth)\b|[.,;]|$)',
        rf'\bsupplier\s+({word}(?:\s+{word}){{0,4}}?)(?=\s+(?:delivered|supplied|sent|brought)\b|[.,;]|$)',
        rf'(?:^|[.;]\s*)({word}(?:\s+{word}){{0,4}}?)\s+(?=(?:supplied|delivered)\b)',
    ]
    supplier_candidates=[]
    for pattern in supplier_patterns:
        for m in re.finditer(pattern,text,flags=re.IGNORECASE):
            value=' '.join(m.group(1).strip().split())
            if value.lower().startswith('supplier '):
                value=value.split(' ',1)[1].strip()
            if value and value.lower() not in {'stock','supplier','cash','rice','goods'}:
                supplier_candidates.append(value)
    unique=[]
    for value in supplier_candidates:
        if value.casefold() not in {x.casefold() for x in unique}:
            unique.append(value)
    if len(unique)==1:
        supplier_name=unique[0]
        if supplier_name.islower() or supplier_name.isupper():
            supplier_name=' '.join(x.capitalize() for x in supplier_name.split())

    return quantity_value,unit,product,total_cost,supplier_name


def _stock_distributed_suggestion(text):
    """Extract one stock issue/distribution with a destination. No sale revenue is implied."""
    lowered=' '.join(str(text or '').strip().lower().split())
    unit_pattern=r'(bags?|crates?|cartons?|pieces?|bottles?|packs?|sacks?|boxes?|units?)'
    lead=r'(?:sent|send|issued|issue|distributed|distribute|transferred|transfer|moved|move)'
    pattern=(
        r'\b'+lead+r'\s+('+NUM+r')\s+'+unit_pattern+
        r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)\s+\bto\b\s+'
        r'([a-z][a-z0-9 &\'’.-]{0,100}?)(?=[.,;]|$)'
    )
    matches=list(re.finditer(pattern,lowered,flags=re.IGNORECASE))
    if len(matches)!=1:
        return None,None,None,None
    m=matches[0]
    q=number(m[1])
    if q is None or Decimal(q)<=0:
        return None,None,None,None
    unit=m[2].lower()
    product=' '.join(m[3].strip(" .,-").split())
    destination=' '.join(m[4].strip(" .,-").split())
    if not product or not destination:
        return None,None,None,None
    # Avoid interpreting a customer-payment destination as an inventory location.
    if re.search(r'\b(?:account|wallet|opay|palmpay|bank)\b',destination):
        return None,None,None,None
    return q,unit,product,destination


def _stock_m3_suggestion(text):
    """Extract conservative M3 inventory events: returns, damage/loss/expiry, and physical counts."""
    lowered=' '.join(str(text or '').strip().lower().split())
    unit=r'(bags?|crates?|cartons?|pieces?|bottles?|packs?|sacks?|boxes?|units?)'
    # Returned to supplier
    m=re.search(r'\breturned?\s+('+NUM+r')\s+'+unit+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)\s+to\s+([a-z][a-z0-9 &\'’.-]{0,100}?)(?=[.,;]|$)',lowered,re.I)
    if m:
        q=number(m[1]); product=' '.join(m[3].strip(' .,-').split()); supplier=' '.join(m[4].strip(' .,-').split())
        return ('returned_supplier',q,m[2].lower(),product,None,supplier)
    # Returned in from branch/customer/location. Support both
    # "returned two bags ... from Onitsha branch" and
    # "Onitsha branch returned two bags ...".
    m=re.search(r'^([a-z][a-z0-9 &\'’.-]{0,100}?)\s+returned\s+('+NUM+r')\s+'+unit+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)(?=[.,;]|$)',lowered,re.I)
    if m:
        origin=' '.join(m[1].strip(' .,-').split()); q=number(m[2]); product=' '.join(m[4].strip(' .,-').split())
        return ('returned_in',q,m[3].lower(),product,origin,None)
    m=re.search(r'\b(?:returned|received\s+back|came\s+back)\s+('+NUM+r')\s+'+unit+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)(?:\s+from\s+([a-z][a-z0-9 &\'’.-]{0,100}?))?(?=[.,;]|$)',lowered,re.I)
    if m and ' to ' not in lowered:
        q=number(m[1]); product=' '.join(m[3].strip(' .,-').split()); origin=' '.join((m[4] or '').strip(' .,-').split()) or None
        return ('returned_in',q,m[2].lower(),product,origin,None)
    # Damaged / expired / lost
    for kind,word in [('damaged','damaged|spoilt|spoiled|broken'),('expired','expired'),('lost','lost|missing|stolen')]:
        patterns=[
          r'\b('+NUM+r')\s+'+unit+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)\s+(?:were\s+|was\s+)?(?:'+word+r')\b',
          r'\b(?:'+word+r')\s+('+NUM+r')\s+'+unit+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)(?=[.,;]|$)'
        ]
        for pat in patterns:
            m=re.search(pat,lowered,re.I)
            if m:
                q=number(m[1]); product=' '.join(m[3].strip(' .,-').split())
                return (kind,q,m[2].lower(),product,None,None)
    # Physical count: "physical count is eleven bags of rice" / "counted eleven bags of rice"
    m=re.search(r'\b(?:physical\s+count(?:\s+is)?|counted|stock\s+count(?:\s+is)?)\s+('+NUM+r')\s+'+unit+r'(?:\s+of)?\s+([a-z][a-z0-9 &\'’.-]{0,60}?)(?=[.,;]|$)',lowered,re.I)
    if m:
        q=number(m[1]); product=' '.join(m[3].strip(' .,-').split())
        return ('physical_count',q,m[2].lower(),product,None,None)
    return (None,None,None,None,None,None)


def _customer_name_suggestion(text):
    """Extract a conservative customer-name suggestion from common trade wording."""
    if not isinstance(text, str):
        return None
    word = r"[A-Za-z][A-Za-z'’.-]*"
    name = rf"({word}(?:\s+{word}){{0,3}})"
    payment_verbs = r"paid|repaid|transferred|sent|credited"
    patterns = [
        rf"\b(?:sold|sell|sale)\b.*?\bto\s+{name}(?=\s+(?:for|at|total|paid|received|cash|on\s+credit|owing|owes)\b|[.,;]|$)",
        rf"\bcustomer\s+{name}(?=\s+(?:bought|paid|repaid|transferred|sent|credited|owes|owing|took|collected|purchased)\b|[.,;]|$)",
        rf"\b(?:payment|repayment)\s+from\s+{name}(?=\s+(?:for|of|is|was|₦|ngn|[0-9])|[.,;]|$)",
        rf"(?:^|[.;]\s*){name}\s+(?=(?:{payment_verbs})\s+(?:me\s+|to\s+me\s+)?(?:₦|ngn\s*)?(?:[0-9]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety))",
        rf"(?:^|[.;]\s*){name}\s+(?=(?:bought|purchased|took|collected)\b)",
    ]
    direct_customer_payment=re.search(
        r'\bcustomer\s+([A-Za-z][A-Za-z\'-]*)\s+'
        r'(?:paid|repaid|transferred|sent|credited)\b',
        text,re.IGNORECASE
    )
    if direct_customer_payment:
        return direct_customer_payment[1].capitalize()

    candidates=[]
    for pattern in patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            value=' '.join(m.group(1).strip().split())
            if value and value.lower() not in {'cash','credit','customer','sale','payment','repayment','i','me','my','we','us','our','you','your'}:
                candidates.append(value)
    unique=[]
    for value in candidates:
        if value.lower() not in {x.lower() for x in unique}:
            unique.append(value)
    if len(unique)!=1:
        return None
    value=unique[0]
    if value.islower() or value.isupper():
        value=' '.join(part.capitalize() for part in value.split())
    return value


def parse(text, selected_kind=None):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise ValueError('Transcript must contain 1–2000 characters.')
    if selected_kind not in (None,'','sale','repayment','stock'):
        raise ValueError('Choose Sale, Customer payment, or Inventory movement.')

    lowered=text.lower().strip();warnings=[]
    received_signal=bool(
        re.search(r'\b(?:received|receive|restocked|restock|stocked|stock\s+received|delivered|supplied)\b',lowered)
        or re.search(r'\b(?:bought|purchased)\s+(?:new\s+)?stock\b',lowered)
    )
    # In explicit sale statements, "received <amount>" describes
    # payment received, not incoming inventory.
    sale_payment_context = bool(
        re.search(r'\b(?:sale|sell|sold)\b', lowered)
        and re.search(
            r'\breceived\s+(?:\u20a6|ngn\s*)?'
            r'(?:[0-9]|one\b|two\b|three\b|four\b|five\b)',
            lowered
        )
    )
    if sale_payment_context:
        received_signal = False

    distributed_signal=bool(
        re.search(r'\b(?:sent|send|issued|issue|distributed|distribute|transferred|transfer|moved|move)\b',lowered)
        and re.search(r'\b(?:bags?|crates?|cartons?|pieces?|bottles?|packs?|sacks?|boxes?|units?)\b',lowered)
        and re.search(r'\bto\b',lowered)
    )
    m3_kind,m3_q,m3_unit,m3_product,m3_destination,m3_supplier=_stock_m3_suggestion(text)
    if not m3_kind:
        count_pattern=(
            r'^i\s+have\s+('+NUM+r')\s+'
            r'(bags?|crates?|cartons?|pieces?|bottles?|packs?|sacks?|boxes?|units?)'
            r'(?:\s+of)?\s+([a-z][a-z0-9 &.-]{0,60}?)\s+in\s+stock[.!]?\s*$'
        )
        count_match=re.search(count_pattern,lowered)
        if count_match:
            m3_kind='physical_count'
            m3_q=number(count_match[1])
            m3_unit=count_match[2]
            m3_product=count_match[3].strip()
            m3_destination=None
            m3_supplier=None
    purchase_signal=bool(
        re.search(r'\b(?:bought|purchased)\s+(?:new\s+)?(?:[0-9]|one\b|two\b|three\b|four\b|five\b)',lowered)
        and not re.search(r'\b(?:customer|client)\s+[a-z]+\s+(?:bought|purchased)\b',lowered)
    )
    inventory_signal=received_signal or distributed_signal or purchase_signal or bool(m3_kind)
    if selected_kind=='stock' or (selected_kind in (None,'') and inventory_signal):
        if re.search(r'\b(not|actually|instead|correction|point|minus|negative)\b',lowered):
            return {
                'kind':'stock','stock_movement':None,'quantity':None,'unit':None,'product':None,
                'total_cost':None,'supplier_name':None,'destination':None,'customer_name':None,
                'warnings':['Correction or unsupported quantity wording detected. Enter and verify the inventory movement manually. Nothing has been saved.']
            }
        if m3_kind:
            label={'returned_supplier':'return to supplier','returned_in':'stock return in','damaged':'damaged stock','expired':'expired stock','lost':'lost/missing stock','physical_count':'physical stock count'}[m3_kind]
            return {
                'kind':'stock','stock_movement':m3_kind,'quantity':m3_q,'unit':m3_unit,'product':m3_product,
                'total_cost':None,'supplier_name':m3_supplier,'destination':m3_destination,'customer_name':None,
                'warnings':[f'{label.title()} was suggested from the note. Verify product, quantity and any supplier/location before saving. Nothing has been saved.']
            }
        if distributed_signal:
            q,unit,product,destination=_stock_distributed_suggestion(text)
            return {
                'kind':'stock','stock_movement':'distributed','quantity':q,'unit':unit,'product':product,
                'total_cost':None,'supplier_name':None,'destination':destination,'customer_name':None,
                'warnings':['Quantity, product and destination were suggested from the note; verify all three before saving. Nothing has been saved.' if q is not None else 'Stock distribution wording was detected, but the movement could not be fully parsed. Enter product, quantity and destination manually. Nothing has been saved.']
            }
        if received_signal or purchase_signal:
            q,unit,product,total_cost,supplier_name=_stock_received_suggestion(text)
            ws=[]
            if q is not None: ws.append('Quantity was suggested from the note; verify it.')
            if total_cost is not None: ws.append('Total purchase cost was suggested from the note; verify it.')
            if supplier_name: ws.append('Supplier name was suggested; verify the supplier before saving.')
            if q is None: ws.append('Enter the quantity received manually.')
            return {
                'kind':'stock','stock_movement':'received','quantity':q,'unit':unit,'product':product,
                'total_cost':total_cost,'supplier_name':supplier_name,'destination':None,'customer_name':None,
                'warnings':ws or ['Review the stock received details. Nothing has been saved.']
            }
        matches=list(re.finditer(r'\bquantity\s*(?:is\s+|:\s*)?('+NUM+r')(?![\w,])',lowered))
        q=number(matches[0][1]) if len(matches)==1 else None
        return {'kind':'stock','stock_movement':None,'quantity':q,'unit':None,'product':None,'total_cost':None,'supplier_name':None,'destination':None,'customer_name':None,'warnings':['Choose an inventory movement and verify the remaining fields. Nothing has been saved.']}

    sale=bool(re.search(r'\b(sale|sell|sold)\b',lowered))
    customer_name=_customer_name_suggestion(text)

    # Nigerian payment-language normalization. These phrases mean money received
    # from the named customer when the sentence subject is the customer and there
    # is no sale wording. We intentionally do not treat bare "transfer"/"send"
    # phrases without a recognized customer as repayments.
    inbound_payment_phrase = bool(
        customer_name and not sale and (
            re.search(r'\b(?:paid|repaid)\b', lowered)
            or re.search(r'\btransferred\b', lowered)
            or re.search(r'\bsent\b', lowered)
            or re.search(r'\bcredited\s+(?:me|my\s+(?:account|wallet))\b', lowered)
        )
    )
    repayment=bool(re.search(r'\b(repayment|repaid)\b|\bcustomer payment\b',lowered) or inbound_payment_phrase)
    inferred='sale' if sale and not repayment else 'repayment' if repayment and not sale else None
    kind=selected_kind or inferred
    base={'kind':kind,'total':None,'paid':None,'quantity':None,'unit':None,'product':None,'customer_name':customer_name}

    if (sale and repayment) or (selected_kind=='sale' and repayment) or (selected_kind=='repayment' and sale):
        return {**base,'kind':None,'warnings':['Transaction choice conflicts with the note. Separate transactions or clarify the type.']}
    if re.search(r'\b(not|actually|correction|instead|previous|yesterday|minus|negative|point)\b|(?<!\w)-\s*\d',lowered):
        return {**base,'warnings':['Correction, historical context or unsupported amount wording detected. Enter the verified amounts manually.']}
    if not kind:warnings.append('Choose Sale or Customer payment before preparing this note.')

    def amount(label):
        hits=list(re.finditer(r'\b(?:'+label+r')\b',lowered))
        if len(hits)!=1:return None
        tail=lowered[hits[0].end():]
        m=re.match(r'\s*(?:(?:is|was|of)\b|:)?\s*(?:₦|ngn\s*)?('+NUM+r')(?![\w,])',tail)
        if not m:return None
        rest=tail[m.end():]
        if re.match(r'\s+(?:\d|point\b|and\b|hundred\b|thousand\b|million\b)',rest):return None
        return number(m[1])

    total=amount('total') if kind=='sale' else '0' if kind=='repayment' else None
    paid=amount('received|paid|repaid|transferred|sent') if kind else None
    if kind and paid is None:
        credited_match = re.search(
            r'\bcredited\s+(?:me|my\s+(?:account|wallet))\s+(?:with\s+)?(?:₦|ngn\s*)?(' + NUM + r')(?![\w,])',
            lowered
        )
        if credited_match:
            paid=number(credited_match[1])
    quantity=unit=product=None

    if kind=='sale':
        quantity,unit,product=_sale_item_suggestion(lowered)
        if quantity is not None:
            warnings.append('Quantity, unit and product were suggested from the note; verify them before saving.')

    items=list(re.finditer(r'(?<![\w-])('+NUM+r')\s+(?:bags?|crates?|cartons?|pieces?|bottles?)\s+of\s+[a-z]+(?:\s+[a-z]+){0,2}?\s*[,;]\s*('+NUM+r')\s*(?:naira\s*)?each\b',lowered))
    if kind=='sale' and len(items)==1:
        q=number(items[0][1]);price=number(items[0][2])
        if q is not None and price is not None and Decimal(q)>0 and Decimal(q)==Decimal(q).to_integral_value():
            computed=number(format(Decimal(q)*Decimal(price),'f'))
            if computed is not None:
                if total is not None and Decimal(total)!=Decimal(computed):
                    total=None;warnings.append('Stated total differs from quantity × price. Enter the verified total.')
                elif not re.search(r'\btotal\b',lowered):
                    total=computed;warnings.append('Total calculated from quantity × unit price; check both against the note.')

    if kind=='sale' and quantity is not None:
        unit_prices=list(re.finditer(
            r'\bat\s+(?:\u20a6|ngn\s*)?('+NUM+r')\s*(?:naira\s*)?each\b',
            lowered
        ))
        if len(unit_prices)==1:
            price=number(unit_prices[0][1])
            if price is not None and Decimal(quantity)>0:
                computed=number(format(Decimal(quantity)*Decimal(price),'f'))
                if computed is not None:
                    if total is None:
                        total=computed
                        warnings.append('Total calculated from quantity and unit price; verify both.')
                    elif Decimal(total)!=Decimal(computed):
                        total=None
                        warnings.append('Unit price conflicts with the stated total. Verify the amount.')

    if kind=='sale':
        for_amount=_unique_amount_after_pattern(lowered,r'\bfor\b')
        if for_amount is not None:
            if total is None:
                total=for_amount;warnings.append('Sale total was suggested from the phrase “for …”; verify the amount before saving.')
            elif Decimal(total)!=Decimal(for_amount):
                total=None;warnings.append('The explicit total conflicts with the amount after “for”. Enter the verified sale total.')
            cash_match=re.search(r'\bfor\s+(?:₦|ngn\s*)?('+NUM+r')(?![\w,])(?:\s+naira)?\s+cash\b',lowered)
            if cash_match and number(cash_match[1])==for_amount:
                if paid is None:
                    paid=for_amount;warnings.append('Cash payment was suggested as fully paid from the same sale amount; verify it.')
                elif Decimal(paid)!=Decimal(for_amount):
                    paid=None;warnings.append('The stated payment conflicts with the cash sale amount. Enter the verified payment.')

    if kind=='repayment' and paid is None:
        direct_paid=re.search(
            r'\b(?:paid|repaid|transferred|sent)\s+'
            r'(?:me\s+|to\s+me\s+)?'
            r'(?:\u20a6|ngn\s*)?('+NUM+r')'
            r'(?![\w,])',
            lowered
        )
        if direct_paid:
            paid=number(direct_paid[1])
            warnings.append(
                'Customer payment amount was suggested; verify it.'
            )

    if kind=='repayment' and paid is None:
        repayment_amount=_unique_amount_after_pattern(
            lowered,
            r'\b(?:customer payment|repayment|repaid|paid|transferred|sent|credited)\b'
        )
        if repayment_amount is not None:
            paid=repayment_amount;warnings.append('Customer payment amount was suggested from the note; verify it before saving.')

    if total is None:warnings.append('What is the total sale amount? Enter it below.')
    if paid is None:warnings.append('How much did the customer pay? Enter it below; use 0 only if nothing was received.')
    if kind=='sale' and total is not None and paid is not None and Decimal(paid)>Decimal(total):
        total=paid=None;warnings.append('Payment exceeds sale total. Clarify both amounts.')
    warnings.append('Select the correct customer and verify every amount. Nothing has been saved.')
    return {'kind':kind,'total':total,'paid':paid,'quantity':quantity,'unit':unit,'product':product,'customer_name':customer_name,'warnings':warnings}
