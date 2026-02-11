FROM python:3.11-slim

WORKDIR /app

# Install piper-tts with HTTP support
RUN pip install --no-cache-dir piper-tts[http]

# Download the voice model specified
# Disable SSL verification to allow corporate VPN certificates
RUN python3 -c "import ssl; ssl._create_default_https_context = ssl._create_unverified_context; import runpy; import sys; sys.argv = ['piper.download_voices', 'en_GB-cori-high']; runpy.run_module('piper.download_voices', run_name='__main__')"

# Expose the default port
EXPOSE 5000

# Start the Piper HTTP server with the selected model
CMD ["sh", "-c", "python3 -m piper.http_server -m en_GB-cori-high"]
