from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/business.manage']
SERVICE_ACCOUNT_FILE = 'service-account-key.json'  # put your key file here

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

service = build('mybusinessaccountmanagement', 'v1', credentials=creds)

# Step 1: Get the service account's own GBP account ID
accounts_response = service.accounts().list().execute()
accounts = accounts_response.get('accounts', [])

if not accounts:
    print("No accounts found for this service account.")
    exit()

for account in accounts:
    account_name = account['name']  # e.g. "accounts/123456789"
    print(f"Checking invitations for: {account_name}")

    invitations_response = service.accounts().invitations().list(
        parent=account_name
    ).execute()

    invitations = invitations_response.get('invitations', [])
    if not invitations:
        print("  No pending invitations.")
        continue

    for invite in invitations:
        print(f"  Found invitation: {invite['name']} | type: {invite.get('targetType')}")
        service.accounts().invitations().accept(name=invite['name'], body={}).execute()
        print(f"  Accepted: {invite['name']}")
