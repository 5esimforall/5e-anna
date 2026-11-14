"""Read source bytes without changing their original encoding or hash."""
from hashlib import sha256


def read_page(path):
    raw=path.read_bytes()
    try:return raw.decode('utf-8-sig'),'utf-8',sha256(raw).hexdigest()
    except UnicodeDecodeError:
        try:return raw.decode('gb18030'),'gb18030-inferred',sha256(raw).hexdigest()
        except UnicodeDecodeError:return raw.decode('utf-8',errors='replace'),'utf-8-replacement',sha256(raw).hexdigest()

