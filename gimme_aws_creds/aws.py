"""
Copyright 2018-present Engie SA.
Licensed under the Apache License, Version 2.0 (the "License");
You may not use this file except in compliance with the License.
You may obtain a copy of the License at
      http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and* limitations under the License.*
"""
import getpass
import re
import sys
import time
import uuid
from codecs import decode
from urllib.parse import parse_qs
from urllib.parse import urlparse
from . import version

import keyring
import requests
from bs4 import BeautifulSoup
from keyring.backends.fail import Keyring as FailKeyring
from keyring.errors import PasswordDeleteError
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

import base64
from collections import namedtuple
import xml.etree.ElementTree as ET

import gimme_aws_creds.common as commondef

class AwsResolver(object):
    """
       The Aws Client Class performes post request on AWS sign-in page
       to fetch friendly names/alias for account and IAM roles
    """

    def __init__(self, verify_ssl_certs=True):
        """
        :param verify_ssl_certs: Enable/disable SSL verification
        """
        self._verify_ssl_certs = verify_ssl_certs

        if verify_ssl_certs is False:
            requests.packages.urllib3.disable_warnings()

        # Allow up to 5 retries on requests to AWS in case we have network issues
        self._http_client = requests.Session()
        retries = Retry(total=5, backoff_factor=1,
                        method_whitelist=['POST'])
        self._http_client.mount('https://', HTTPAdapter(max_retries=retries))

    def get_signinpage(self, saml_token, saml_target_url):
        """ Post SAML token to aws sign in page and get back html result"""
        payload = {
            'SAMLResponse': saml_token,
            'RelayState': ''
        }
        
        response = self._http_client.post(
            saml_target_url,
            data=payload,
            verify=self._verify_ssl_certs
        )
        return response.text


    def _enumerate_saml_roles(self, assertion, saml_target_url):
        signin_page = self.get_signinpage(assertion, saml_target_url)
        
        """ using the assertion to fetch aws sign-in page, parse it and return aws sts creds """
        role_pairs = []
        root = ET.fromstring(base64.b64decode(assertion))
        for saml2_attribute in root.iter('{urn:oasis:names:tc:SAML:2.0:assertion}Attribute'):
            if saml2_attribute.get('Name') == 'https://aws.amazon.com/SAML/Attributes/Role':
                for saml2_attribute_value in saml2_attribute.iter('{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue'):
                    role_pairs.append(saml2_attribute_value.text)

        # build a temp hash table
        table = {}
        for role_pair in role_pairs:
            idp, role = None, None
            for field in role_pair.split(','):
                if 'saml-provider' in field:
                    idp = field
                elif 'role' in field:
                    role = field
            if not idp or not role:
                print('Parsing error on {}'.format(role_pair))
                exit()
            else:
                table[role] = idp
        
        # init parser
        soup = BeautifulSoup(signin_page, 'html.parser')
        
        # find all roles
        roles = soup.find_all("div", attrs={"class": "saml-role"})
        # Normalize pieces of string;
        result = []
        for role_item in roles:
            idp, role, friendly_account_name, friendly_role_name = None, None, None, None
            role = role_item.label['for']
            idp = table[role]
            friendly_account_name = role_item.parent.parent.find("div").find("div").get_text()
            friendly_role_name = role_item.label.get_text()
            result.append(commondef.RoleSet(idp=idp, role=role, friendly_account_name=friendly_account_name, friendly_role_name=friendly_role_name))
        return result

    def _display_role(self, roles):
        """ gets a list of available roles and
        asks the user to select the role they want to assume
        """
        # Gather the roles available to the user.
        role_strs = []
        last_account = None
        for i, role in enumerate(roles):
            if not role:
                continue
            current_account = role.friendly_account_name
            if not current_account ==  last_account:
                role_strs.append(current_account)
                last_account=current_account
                
            role_strs.append('      [ {} ]: {}'.format(i, role.friendly_role_name))

        return role_strs
