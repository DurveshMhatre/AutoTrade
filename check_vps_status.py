import paramiko
import sys

def execute_remote(host, username, password, command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, username=username, password=password, timeout=10)
        stdin, stdout, stderr = client.exec_command(command)
        print(stdout.read().decode('utf-8'))
    except Exception as e:
        print(f"Connection failed: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    execute_remote("77.42.69.63", "root", "umCgMvCVjenE", "pm2 logs TradingBot --lines 15 --nostream")
