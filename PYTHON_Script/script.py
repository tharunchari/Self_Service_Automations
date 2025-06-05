import os
 
def main():
    secret_value = os.getenv("SECRET_CODE_GITHUB")
    if secret_value:
        # For demo purposes, just print masked confirmation
        print("✅ Secret is available and has been read into Python.")
        print(f"Secret length: {len(secret_value)} characters")
    else:
        print("❌ Secret is missing or not set in environment.")
 
if __name__ == "__main__":
    main()
