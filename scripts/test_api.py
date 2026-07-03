import urllib.request
import json

data = json.dumps({
    'query': 'Police arrested neighbor without warrant for petty theft. What are the rights under BNS?', 
    'language': 'en'
}).encode('utf-8')

try:
    print("Sending English test query to server...")
    req = urllib.request.Request('http://localhost:8000/api/query', data=data, headers={'Content-Type': 'application/json'})
    res = urllib.request.urlopen(req).read().decode('utf-8')
    
    with open('test_output.json', 'w') as f:
        f.write(res)
    print("✅ Success! Output saved to 'test_output.json'")
except Exception as e:
    print(f"❌ Failed to query backend server: {e}\nMake sure uvicorn backend is running on port 8000.")
