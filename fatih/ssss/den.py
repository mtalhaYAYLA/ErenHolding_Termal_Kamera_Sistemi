 # - *- coding: utf-8 -*
import requests
request_url = 'http://192.168.1.64:80/ISAPI/System/deviceInfo'
# Set the authentication information
auth = requests.auth.HTTPDigestAuth('admin', 'ErenEnerji')
# Send the request and receive response
response = requests.get(request_url, auth=auth)
# Output response content
print(response.text)