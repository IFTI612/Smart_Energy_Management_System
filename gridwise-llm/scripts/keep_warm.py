import argparse
import time
import httpx

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/health")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    print(f"Keeping warm {args.url} every {args.interval} seconds...")
    while True:
        try:
            r = httpx.get(args.url, timeout=5.0)
            print(f"[{time.strftime('%X')}] Ping: {r.status_code}")
        except Exception as e:
            print(f"[{time.strftime('%X')}] Ping failed: {e}")
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
