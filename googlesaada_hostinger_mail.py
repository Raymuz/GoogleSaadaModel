"""GoogleSaada Hostinger Mail API transport, adapted from NTM Email Sender 2.0.1.
Only GoogleSaada env keys are read. No NTM config scanning or automatic sends.
"""
from __future__ import annotations
import os
import re
import requests

MAIL_API='https://api.mail.hostinger.com/api/v1'
EMAIL_RE=re.compile(r'^[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+$')

def _credentials():
    token=os.environ.get('GS_HOSTINGER_API_TOKEN','').strip()
    sender=os.environ.get('GS_MAIL_FROM','').strip()
    if not token or not EMAIL_RE.fullmatch(sender):
        raise RuntimeError('GoogleSaada Hostinger mail is not configured')
    return token,sender

def send_google_saada_mail(to, subject, body):
    """Send single transactional text email; fail closed and do not expose provider body."""
    token,sender=_credentials()
    if not EMAIL_RE.fullmatch(str(to).strip()):
        raise ValueError('Invalid recipient')
    if not (1 <= len(str(subject)) <= 160 and 1 <= len(str(body)) <= 12000):
        raise ValueError('Invalid email content')
    headers={'Authorization':'Bearer '+token, 'Accept':'application/json',
             'Content-Type':'application/json','User-Agent':'GoogleSaada/8H'}
    try:
        res=requests.get(MAIL_API+'/me',headers=headers,timeout=15)
        if not 200 <= res.status_code < 300:
            raise RuntimeError('Hostinger mailbox lookup rejected')
        payload=res.json()
        data=payload.get('data',{}) if isinstance(payload,dict) else {}
        mailboxes=data.get('mailboxes',[]) if isinstance(data,dict) else data if isinstance(data,list) else []
        # Never silently send through a different mailbox on the NTM account.
        matched=next((m for m in mailboxes if isinstance(m,dict)
            and str(m.get('address','')).casefold()==sender.casefold()),None)
        if not matched:
            raise RuntimeError('Configured GoogleSaada sender mailbox not present')
        mailbox_id=matched.get('resourceId') or matched.get('id')
        if not mailbox_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',str(mailbox_id)):
            raise RuntimeError('Invalid Hostinger mailbox identifier')
        response=requests.post(
            MAIL_API+'/mailboxes/'+str(mailbox_id)+'/send',headers=headers,
            json={'to':[str(to).strip()],'subject':str(subject),'text':str(body)},timeout=15)
        if not 200 <= response.status_code < 300:
            raise RuntimeError('Hostinger rejected the email request')
        return {'status':'accepted','http_status':response.status_code}
    except requests.RequestException as exc:
        raise RuntimeError('Hostinger mail network error') from None
