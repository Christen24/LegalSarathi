import urllib.request
import json

data = json.dumps({
    'query': 'पुलिस ने बिना वारंट गिरफ्तार किया', 
    'language': 'hi'
}).encode('utf-8')

try:
    print("Sending test query to server...")
    req = urllib.request.Request('http://localhost:8000/api/query', data=data, headers={'Content-Type': 'application/json'})
    res = urllib.request.urlopen(req).read().decode('utf-8')
    
    with open('test_hindi_output.json', 'w', encoding='utf-8') as f:
        f.write(res)
    print("✅ Success! Output saved to 'test_hindi_output.json'")
except Exception as e:
    print(f"❌ Failed to query backend server: {e}\nMake sure uvicorn backend is running on port 8000.")
