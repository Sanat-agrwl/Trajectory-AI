#!/usr/bin/env python3
"""
OAuth 2.0 Authorization Helper - Complete Setup with Browser
"""

import os
import pickle
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.readonly'
]

def authorize():
    """Authorize and save credentials"""
    
    token_file = 'token.pickle'
    oauth_file = 'oauth_credentials.json'
    
    print("\n" + "="*70)
    print("GOOGLE OAUTH 2.0 AUTHORIZATION")
    print("="*70)
    
    # Check if already authorized
    if os.path.exists(token_file):
        print(f"\n✓ Token already exists: {token_file}")
        print("  You're already authorized!")
        print("\n  To re-authorize, delete token.pickle and run this again:")
        print("    rm token.pickle")
        print("    python authorize_oauth.py")
        return True
    
    # Check if oauth_credentials.json exists  
    if not os.path.exists(oauth_file):
        print(f"\n✗ Missing {oauth_file}")
        print("\n  Make sure you downloaded OAuth credentials from Google Cloud Console")
        return False
    
    print(f"\n✓ Found {oauth_file}")
    
    print("\n" + "-"*70)
    print("IMPORTANT: A browser will open for authorization")
    print("-"*70)
    
    print("\nWhen the browser opens:")
    print("  1. Sign in with your Google account")
    print("  2. Click 'Allow' for Gmail and Calendar access")
    print("  3. You should see 'authentication completed'")
    print("  4. The browser can then be closed")
    
    print("\nStarting authorization flow...")
    print("")
    
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            oauth_file, 
            SCOPES
        )
        
        # Run local server for OAuth callback
        # Note: redirect_uri must match what's in oauth_credentials.json
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            authorization_prompt_message='Please log in and authorize access to Gmail and Calendar'
        )
        
        # Save token
        with open(token_file, 'wb') as f:
            pickle.dump(creds, f)
        
        print("\n" + "="*70)
        print("✓ AUTHORIZATION SUCCESSFUL")
        print("="*70)
        print(f"\n✓ Token saved to: {token_file}")
        print("\nYou can now run your benchmark:")
        print("  python task.py")
        print("\nYou won't need to authorize again unless you delete token.pickle\n")
        return True
        
    except Exception as e:
        print(f"\n✗ Authorization failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Make sure oauth_credentials.json is the correct file")
        print("  2. Ensure Gmail and Calendar APIs are enabled in Google Cloud")
        print("  3. Try again: python authorize_oauth.py")
        return False


if __name__ == "__main__":
    success = authorize()
    exit(0 if success else 1)
