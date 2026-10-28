import gradio as gr
import subprocess
import threading
import sys

def run_trading_bot():
    print("Starting AutoTrade bot in paper mode...", flush=True)
    subprocess.run([sys.executable, "main.py", "--paper"])

# Start trading bot in background
threading.Thread(target=run_trading_bot, daemon=True).start()

def get_status():
    return "AutoTrade Bot is running 24/7 in Paper Mode!"

with gr.Blocks(title="AutoTrade Bot") as demo:
    gr.Markdown("# 🤖 AutoTrade Bot Dashboard")
    status_output = gr.Textbox(value="Bot initialized and running...", label="Status", interactive=False)
    refresh_btn = gr.Button("Refresh Status")
    refresh_btn.click(fn=get_status, outputs=status_output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)