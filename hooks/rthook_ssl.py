"""
PyInstaller runtime hook — fix SSL certificate path in frozen exe.
Must run before any application code. Sets SSL_CERT_FILE so httpx
can validate HTTPS connections to OpenRouter, Groq, Mistral, etc.
Without this every HTTPS request fails → HTTP 500 Internal proxy error.
"""
import os, sys

_meipass = getattr(sys, "_MEIPASS", None)
if _meipass:
    cert = os.path.join(_meipass, "certifi", "cacert.pem")
    if os.path.isfile(cert):
        os.environ["SSL_CERT_FILE"]      = cert
        os.environ["REQUESTS_CA_BUNDLE"] = cert