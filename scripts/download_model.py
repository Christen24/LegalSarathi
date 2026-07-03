import os
from huggingface_hub import hf_hub_download

def download_legal_model():
    repo_id = "invincibleambuj/Ambuj-Tripathi-Indian-Legal-Llama-GGUF"
    filename = "legal-llama-1b.gguf"  # Update if different in repo
    local_dir = "backend/data/models"
    
    os.makedirs(local_dir, exist_ok=True)
    
    print(f"Downloading llama-3.2-1b-instruct.Q4_K_M.gguf from {repo_id}...")
    try:
        path = hf_hub_download(
            repo_id=repo_id,
            filename="llama-3.2-1b-instruct.Q4_K_M.gguf",
            local_dir=local_dir,
            local_dir_use_symlinks=False
        )
        # Rename to match config if needed
        target_path = os.path.join(local_dir, "legal-llama-1b.gguf")
        if os.path.exists(path) and path != target_path:
            print("Renaming model to legal-llama-1b.gguf...")
            os.rename(path, target_path)
            
        print(f"✅ Success! Model saved to: {target_path}")
    except Exception as e:
        print(f"Download failed: {e}")

if __name__ == "__main__":
    download_legal_model()
