import requests
import time
import os

# Get port from environment, default to 9000
PORT = os.environ.get("PORT", 9000)
APP_URL = f"http://localhost:{PORT}"

def ping_app():
    print(f"Starting ping service for {APP_URL}")
    while True:
        try:
            response = requests.get(f"{APP_URL}/health")
            if response.status_code == 200:
                print(f"Ping to {APP_URL}/health successful at {time.ctime()}")
            else:
                print(f"Ping to {APP_URL}/health failed with status code {response.status_code} at {time.ctime()}")
        except requests.exceptions.RequestException as e:
            print(f"An error occurred: {e} at {time.ctime()}")
        # Wait for 5 minutes (300 seconds)
        time.sleep(300)

if __name__ == "__main__":
    ping_app()
