import os
 
def main():
    secret = os.getenv("ANSIBLE_PASSWORD")
    if secret:
        # Never print the raw secret! This is just for demonstration.
        print("✅ SECRET_CODE_GITHUB is set.")
        print(f"Secret value (masked preview): {secret[:2]}****{secret[-2:]}")
    else:
        print("❌ SECRET_CODE_GITHUB is not set.")
 
if __name__ == "__main__":
    main()
