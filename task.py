import asyncio
import json
import os
from typing import Dict, Any, Callable
from datetime import datetime, timedelta
from dotenv import load_dotenv
from composio import Composio
from composio_claude_agent_sdk import ClaudeAgentSDKProvider
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, create_sdk_mcp_server, AssistantMessage, TextBlock

# Import Google APIs
import pickle
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

# OAuth 2.0 configuration
OAUTH_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.readonly'
]

# ===== GOOGLE API HELPERS =====

class GoogleAPIClient:
    """Manages Google API authentication and requests"""
    
    def __init__(self):
        self.gmail_service = None
        self.calendar_service = None
        self.creds = None
        self._init_services()
    
    def _get_oauth_credentials(self):
        """Get OAuth 2.0 credentials from token if it exists"""
        token_file = 'token.pickle'
        
        # Only try to use OAuth if token already exists
        # (don't try to create new tokens - use service account instead)
        if os.path.exists(token_file):
            try:
                with open(token_file, 'rb') as token:
                    creds = pickle.load(token)
                    if creds and creds.valid:
                        return creds
            except Exception:
                pass
        
        return None
    
    def _init_services(self):
        """Initialize Gmail and Calendar services"""
        try:
            # Try OAuth 2.0 first
            self.creds = self._get_oauth_credentials()
            
            # Fallback to service account if OAuth not available
            if not self.creds:
                creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
                if creds_file and os.path.exists(creds_file):
                    self.creds = Credentials.from_service_account_file(creds_file)
                    print("  ⚠ Using service account (OAuth 2.0 not available)")
                else:
                    print("  ⚠ No Google credentials found")
                    return
            
            if self.creds:
                self.gmail_service = build('gmail', 'v1', credentials=self.creds)
                self.calendar_service = build('calendar', 'v3', credentials=self.creds)
                print("  ✓ Google API services initialized")
        except Exception as e:
            print(f"  ⚠ Failed to initialize Google API: {str(e)}")
    
    async def get_recent_emails(self, query: str, max_results: int = 10) -> list:
        """Fetch recent emails matching a query"""
        try:
            if not self.gmail_service:
                print("  ⚠ Gmail service not available")
                return []
            
            results = self.gmail_service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            email_data = []
            
            for msg in messages:
                msg_data = self.gmail_service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()
                
                headers = msg_data['payload']['headers']
                email_info = {
                    'id': msg['id'],
                    'to': next((h['value'] for h in headers if h['name'] == 'To'), ''),
                    'from': next((h['value'] for h in headers if h['name'] == 'From'), ''),
                    'subject': next((h['value'] for h in headers if h['name'] == 'Subject'), ''),
                    'date': next((h['value'] for h in headers if h['name'] == 'Date'), '')
                }
                email_data.append(email_info)
            
            return email_data
        except HttpError as error:
            print(f"  ⚠ Gmail API error: {error}")
            return []
    
    async def get_calendar_events(self, calendar_id: str = 'primary', time_min: str = None, time_max: str = None) -> list:
        """Fetch calendar events within a time range"""
        try:
            if not self.calendar_service:
                print("  ⚠ Calendar service not available")
                return []
            
            kwargs = {
                'calendarId': calendar_id,
                'maxResults': 50
            }
            
            if time_min:
                kwargs['timeMin'] = time_min
            if time_max:
                kwargs['timeMax'] = time_max
            
            results = self.calendar_service.events().list(**kwargs).execute()
            events = results.get('items', [])
            
            event_data = []
            for event in events:
                event_info = {
                    'id': event.get('id', ''),
                    'title': event.get('summary', ''),
                    'start': event.get('start', {}).get('dateTime', event.get('start', {}).get('date', '')),
                    'end': event.get('end', {}).get('dateTime', event.get('end', {}).get('date', '')),
                    'organizer': event.get('organizer', {}).get('email', '')
                }
                event_data.append(event_info)
            
            return event_data
        except HttpError as error:
            print(f"  ⚠ Calendar API error: {error}")
            return []


# ===== FAILING TASKS =====

FAILING_TASKS = [
    {
        "id": "task_1",
        "instruction": "Send an email to sanat@example.com with subject 'Important' and body 'Please review urgently'.",
        "failure_reason": "Email address does not exist in the system. Agent will fail if it doesn't validate recipient.",
        "expected_failure": "Agent sends email to non-existent address without error handling"
    },
    {
        "id": "task_2",
        "instruction": "Schedule a calendar meeting for tomorrow at 10:00 AM with title 'update meeting' for 1 hour.",
        "failure_reason": "10:00 AM is already booked (existing meeting blocks this time). Agent doesn't check conflicts.",
        "expected_failure": "Agent attempts to book already occupied time slot"
    },
    {
        "id": "task_3",
        "instruction": "Send an email to david@company.com saying 'Can we schedule a meeting for 10:00 AM today? and add it to calender '",
        "failure_reason": "It's already 1:25 PM. Agent should recognize past time and fail gracefully.",
        "expected_failure": "Agent tries to schedule meeting in the past"
    },
]


class RewardFunction:
    
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.composio = Composio(provider=ClaudeAgentSDKProvider())
        self.session = self.composio.create(user_id="reward_validator")
        self.task = next((t for t in FAILING_TASKS if t["id"] == task_id), None)
        self.api_client = GoogleAPIClient()
    
    async def evaluate(self, agent_response: str) -> Dict[str, Any]:
   
        if not self.task:
            return {"score": 0.0, "error": "Task not found"}
        
        print("\n" + "="*70)
        print("REWARD FUNCTION VALIDATION (API CHECKS)")
        print("="*70)
        
        result = {
            "task_id": self.task_id,
            "task_instruction": self.task["instruction"],
            "expected_failure": self.task["expected_failure"],
            "failure_reason": self.task["failure_reason"],
            "checks": {},
            "failure_detected": False,
            "score": 0.0
        }
        
        # Validate based on task type
        if self.task_id == "task_1":
            result["checks"] = await self._validate_email_failure()
        elif self.task_id == "task_2":
            result["checks"] = await self._validate_calendar_conflict()
        elif self.task_id == "task_3":
            result["checks"] = await self._validate_past_time_failure()
        # Check if failure was properly detected
        result["failure_detected"] = result["checks"].get("failed_as_expected", False)
        result["score"] = 1.0 if result["failure_detected"] else 0.0
        
        return result
    
    async def _validate_email_failure(self) -> Dict[str, Any]:
        """
        TASK 1: Check if email to sanat@example.com was sent.
        FAILURE: Email was sent to non-existent address (should have been rejected)
        """
        print("\n[TASK 1] REAL API CHECK: Validating email to non-existent address...")
        print("  - Looking for emails sent to: sanat@example.com")
        
        checks = {
            "api_call_made": False,
            "email_found": False,
            "failed_as_expected": False
        }
        
        try:
            # Query Gmail for emails sent tosanat@example.com
            print("  ✓ Querying Gmail API...")
            
            sent_emails = await self.api_client.get_recent_emails(
                query='to:sanat@example.com',
                max_results=10
            )
            
            checks["api_call_made"] = True
            
            if sent_emails:
                print(f"  ✓ FOUND {len(sent_emails)} email(s) tosanat@example.com")
                for email in sent_emails:
                    print(f"    • To: {email.get('to', '')}")
                    print(f"    • Subject: {email.get('subject', '')}")
                    print(f"    • Date: {email.get('date', '')}")
                
                checks["email_found"] = True
                print(f"\n  ✓ FAILURE DETECTED: Agent sent emails to non-existent address!")
                print(f"    This proves the task FAILED - agent didn't validate recipient")
                checks['failed_as_expected'] = True
            else:
                print("  ✗ NO emails found tosanat@example.com")
                print("    System properly rejected the invalid address")
                checks['failed_as_expected'] = False
            
        except Exception as e:
            print(f"  ⚠ Error making API call: {str(e)}")
            checks["error"] = str(e)
            checks["failed_as_expected"] = False
        
        return checks
    
    async def _validate_calendar_conflict(self) -> Dict[str, Any]:
        """
        TASK 2: Check if calendar conflict was properly detected.
        FAILURE: "update meeting" created at 10:00 AM despite conflict
        """
        print("\n[TASK 2] REAL API CHECK: Validating calendar conflict...")
        print("  - Checking for 'update meeting' events on tomorrow (10:00 AM)")
        
        checks = {
            "api_call_made": False,
            "events_found": [],
            "update_meeting_count": 0,
            "conflict_detected": False,
            "failed_as_expected": False
        }
        
        try:
            # Get ALL events to see what's actually in the calendar
            print("  ✓ Querying Calendar API for all upcoming events...")
            
            all_events = await self.api_client.get_calendar_events()
            
            checks["api_call_made"] = True
            print(f"  ✓ Found {len(all_events)} total events in calendar")
            
            # Look for "update meeting" events
            update_meetings = [e for e in all_events 
                             if 'update meeting' in e.get('title', '').lower()]
            
            print(f"  ✓ Found {len(update_meetings)} 'update meeting' event(s)")
            
            for event in update_meetings:
                print(f"    • Title: {event.get('title', 'Untitled')}")
                print(f"    • Start: {event.get('start', '')}")
                print(f"    • End: {event.get('end', '')}")
                checks["events_found"].append({
                    'title': event.get('title'),
                    'start': event.get('start'),
                    'end': event.get('end')
                })
            
            # Multiple "update meeting" events = CONFLICT
            if len(update_meetings) > 1:
                print(f"\n  ✓ FAILURE DETECTED: Multiple 'update meeting' events at same time!")
                print(f"    This proves the task FAILED - agent ignored calendar conflict")
                checks["update_meeting_count"] = len(update_meetings)
                checks["conflict_detected"] = True
                checks['failed_as_expected'] = True
            elif len(update_meetings) == 1:
                # Check if there are OTHER events at the same time
                update_event = update_meetings[0]
                update_start = update_event.get('start', '')
                
                # Count events during same time
                same_time_events = [e for e in all_events 
                                   if e.get('start') == update_start and e.get('id') != update_event.get('id')]
                
                if same_time_events:
                    print(f"\n  ✓ FAILURE DETECTED: 'update meeting' created despite existing event!")
                    for e in same_time_events:
                        print(f"    • Conflicting: {e.get('title', 'Untitled')} at {e.get('start', '')}")
                    checks["conflict_detected"] = True
                    checks['failed_as_expected'] = True
                else:
                    print(f"  ✗ Only one 'update meeting' found, no obvious conflict")
                    checks['failed_as_expected'] = False
            else:
                print(f"  ✗ No 'update meeting' events found")
                checks['failed_as_expected'] = False
            
        except Exception as e:
            print(f"  ⚠ Error making API call: {str(e)}")
            checks["error"] = str(e)
            checks["failed_as_expected"] = False
        
        return checks
    
    async def _validate_past_time_failure(self) -> Dict[str, Any]:
        """
        TASK 4: Check if scheduling in past was rejected.
        Real check: Compare current time with requested time.
        """
        print("\n[TASK 4] Checking past time scheduling FAILURE...")
        print("  - Validating: 10:00 AM today should be REJECTED (already passed)")
        
        checks = {
            "current_time": datetime.now().strftime('%I:%M %p'),
            "requested_time": "10:00 AM (today)",
            "time_in_past": False,
            "scheduling_allowed": False,
            "event_created": False,
            "failed_as_expected": False
        }
        
        try:
            current_time = datetime.now()
            requested_time = datetime.now().replace(hour=10, minute=0, second=0)
            
            print(f"  ✓ Current time: {current_time.strftime('%I:%M %p')}")
            print(f"  ✓ Requested time: {requested_time.strftime('%I:%M %p')} (today)")
            
            # Check if requested time is in past
            if current_time > requested_time:
                checks["time_in_past"] = True
                print(f"  ✓ Time is in the PAST - scheduling should fail")
                
                # Check if event was created despite being in past
                all_events = await self.api_client.get_calendar_events()
                past_event = next((e for e in all_events 
                                  if 'AM' in str(e.get('start', '')) and '10' in str(e.get('start', ''))), None)
                
                if past_event:
                    print(f"  ✗ Event was created despite being in past (WRONG)")
                    checks['event_created'] = True
                    checks['failed_as_expected'] = False
                else:
                    print(f"  ✓ Event was NOT created (correctly rejected)")
                    checks['failed_as_expected'] = True
            else:
                print(f"  ⚠ Current time is before 10:00 AM (test must run after 10:00 AM)")
                checks["time_in_past"] = False
                checks['failed_as_expected'] = False
                
        except Exception as e:
            print(f"  ⚠ Error checking time/calendar: {str(e)}")
            checks["error"] = str(e)
            checks["failed_as_expected"] = False
        
        return checks

async def run_single_task(task_index: int):
    """
    Run a single failing task and capture the result.
    """
    if task_index < 0 or task_index >= len(FAILING_TASKS):
        print(f"Invalid task index: {task_index}")
        return None
    
    task = FAILING_TASKS[task_index]
    print(f"\n{'='*70}")
    print(f"TASK {task_index + 1}: {task['id']}")
    print(f"{'='*70}")
    print(f"Instruction: {task['instruction']}")
    print(f"Why it should fail: {task['failure_reason']}")
    print(f"Expected failure: {task['expected_failure']}")
    print(f"{'-'*70}\n")
    
    composio = Composio(provider=ClaudeAgentSDKProvider())
    user_id = f"task_runner_{task['id']}"
    session = composio.create(user_id=user_id)
    tools = session.tools()
    
    custom_server = create_sdk_mcp_server(name="composio", version="1.0.0", tools=tools)
    
    options = ClaudeAgentOptions(
        system_prompt="You are a helpful assistant with access to Email, Calendar, and Notion tools. Complete the task given.",
        permission_mode="bypassPermissions",
        mcp_servers={"composio": custom_server},
    )
    
    agent_response = ""
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(task['instruction'])
            
            print("Agent Response:")
            print("-" * 70)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(block.text)
                            agent_response += block.text
            print("-" * 70)
    
    except Exception as e:
        print(f"Error: {str(e)}")
        agent_response = f"Error: {str(e)}"
    
    # Validate using reward function
    reward_fn = RewardFunction(task['id'])
    validation_result = await reward_fn.evaluate(agent_response)
    
    # Print validation results
    print("\n" + "="*70)
    print("VALIDATION REPORT")
    print("="*70)
    print(f"Failure Detected: {'✓ YES' if validation_result['failure_detected'] else '✗ NO'}")
    print(f"Score: {validation_result['score']:.1f}")
    
    if validation_result['checks']:
        print(f"\nChecks Performed:")
        for check_name, check_value in validation_result['checks'].items():
            if check_name != "failed_as_expected":
                print(f"  • {check_name}: {check_value}")
    
    if validation_result['failure_detected']:
        print(f"\n✓ RESULT: Task FAILED AS EXPECTED")
        print(f"  Score: 1 (PASS - Benchmark correctly detected failure)")
    else:
        print(f"\n✗ RESULT: Task SUCCEEDED when it should have FAILED")
        print(f"  Score: 0 (FAIL - Benchmark did not catch the error)")
    
    print("="*70 + "\n")
    
    return {
        "task_id": task['id'],
        "instruction": task['instruction'],
        "failure_reason": task['failure_reason'],
        "agent_response_preview": agent_response[:150],
        "validation": validation_result,
        "reward_score": validation_result['score']
    }


async def run_all_failing_tasks():
    """
    Run all failing tasks one by one and measure how many fail as expected.
    """
    print("\n" + "="*70)
    print("FAILING TASK BENCHMARK - REWARD EVALUATION")
    print("Testing if LLM tasks fail as expected")
    print("="*70)
    
    results = []
    scores = []
    
    for i in range(len(FAILING_TASKS)):
        try:
            result = await run_single_task(i)
            if result:
                results.append(result)
                scores.append(result['reward_score'])
        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Stopping task execution.")
            break
        except Exception as e:
            print(f"Error running task: {str(e)}")
            continue
    
    # Summary with scores
    print("\n" + "="*70)
    print("BENCHMARK SUMMARY")
    print("="*70)
    print(f"Total tasks: {len(FAILING_TASKS)}")
    print(f"Tasks executed: {len(results)}")
    
    if scores:
        passed = int(sum(scores))
        total = len(scores)
        print(f"\nResults:")
        print(f"  Tasks that failed as expected (PASS): {passed}/{total}")
        print(f"  Tasks that succeeded wrongly (FAIL): {total - passed}/{total}")
        
        if passed == total:
            print(f"\n✓ ALL TESTS PASSED - Benchmark correctly detected all failures!")
        else:
            print(f"\n⚠ Some tests failed - Benchmark missed {total - passed} failure case(s)")
    
    print(f"\n{'Task':<10} {'Result':<35} {'Score':<10}")
    print("-" * 55)
    
    for result in results:
        status = "✓ Failed as expected" if result['validation']['failure_detected'] else "✗ Succeeded wrongly"
        score = "1 (PASS)" if result['reward_score'] == 1.0 else "0 (FAIL)"
        print(f"{result['task_id']:<10} {status:<35} {score:<10}")
    
    print("="*70 + "\n")
    
    return results


async def run_task_by_id(task_id: str):
    """
    Run a specific task by ID.
    """
    task_index = next((i for i, t in enumerate(FAILING_TASKS) if t['id'] == task_id), None)
    if task_index is None:
        print(f"Task '{task_id}' not found")
        return None
    
    return await run_single_task(task_index)


async def main():
    import sys
    
    if len(sys.argv) > 1:
        # Run specific task
        task_id = sys.argv[1]
        await run_task_by_id(task_id)
    else:
        # Run all tasks
        await run_all_failing_tasks()


if __name__ == "__main__":
    asyncio.run(main())
