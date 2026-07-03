import subprocess
import sys
import platform

def check_hw():
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Processor: {platform.processor()}")
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)
        print(f"RAM: {ram_gb} GB")
    except ImportError:
        print("RAM: Install 'psutil' to check memory size")
    
    # Check for NVIDIA GPU
    try:
        smi = subprocess.check_output(["nvidia-smi"], stderr=subprocess.STDOUT).decode()
        if "NVIDIA-SMI" in smi:
            print("GPU: NVIDIA (CUDA detected)")
            return "CUDA"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Check for Apple Silicon (Metal)
    if platform.system() == "Darwin":
        if "arm" in platform.processor().lower():
            print("GPU: Apple Silicon (Metal detected)")
            return "METAL"
    
    print("GPU: None (CPU Only)")
    return "CPU"

if __name__ == "__main__":
    hw = check_hw()
    print(f"\nRecommended llama-cpp-python install:")
    if hw == "CUDA":
        print('CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python')
    elif hw == "METAL":
        print('CMAKE_ARGS="-DLLAMA_METAL=on" pip install llama-cpp-python')
    else:
        print('pip install llama-cpp-python')
