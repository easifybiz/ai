import requests
import json

url = "https://www.ulip.dpiit.gov.in/ulip/v1.0.0/VAHAN/01"

payload = json.dumps({
  "vehiclenumber": "DL3CAB1234"
})

headers = {
  'Content-Type': 'application/json',
  'Authorization': 'Bearer eyJhbGciOiJIUzUxMiJ9.eyJhcHBzIjoiYXBwR2F0ZXdheSIsInN1YiI6ImhlbHBkZXNrX3VzciIsImlhdCI6MTczNzM0NzI4NX0.I6Q9OMKjzJjPaSvAl7VHObahT7zcMp6SCB4XfUUXbnEpJDskeYJmj2XrlcksAYFgwv4q_HbhN-y5HDt7s8ABdw'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)