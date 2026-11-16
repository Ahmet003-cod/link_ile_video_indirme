import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
timeout = 120
graceful_timeout = 120
workers = 1
threads = 4
