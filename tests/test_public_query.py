import unittest

from personal_agent.public_query import validate_public_query


class PublicQueryTests(unittest.TestCase):
    """The credential tripwire every outbound ``web_search`` passes (#23)."""

    def test_private_or_credential_derived_queries_are_rejected(self):
        cases=[
            ('private memo text','memory'),
            ('compare /Users/alice/private/receipt.pdf','owner_public_request'),
            ('api_key=super-secret-value','owner_public_request'),
            # A credential must be found at any occurrence. An allowlisted
            # topic word in an earlier Basic/Bearer position must not shield a
            # later credential from inspection.
            ('basic room rates Basic dTpw','owner_public_request'),
            ('bearer authentication examples bearer abcdefghijklmnop','owner_public_request'),
            ('Bearer: secret-token','public_task_input'),
            ('Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.private.signature','owner_public_request'),
            ('Bearer eyJhbGciOiJIUzI1NiJ9.private.signature','owner_public_request'),
            ('access token ghp_1234567890abcdefghijklmnop','owner_public_request'),
            ('ghp_1234567890abcdefghijklmnop','public_task_input'),
            ('password hunter2','owner_public_request'),
            ('password correcthorsebatterystaple','owner_public_request'),
            ('api key abcdefghijklmnopqrstuv','owner_public_request'),
            ('access token longalphabeticvalue','public_task_input'),
            ('password is correcthorsebatterystaple','owner_public_request'),
            ('api key is abcdefghijklmnop','owner_public_request'),
            ('Authorization Basic dXNlcjpwYXNz','owner_public_request'),
            ('sk_' + 'live_abcdefghijklmnopqrstuvwxyz','owner_public_request'),
            ('rk_' + 'live_abcdefghijklmnopqrstuvwxyz','owner_public_request'),
            ('AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456','owner_public_request'),
            ('hf_abcdefghijklmnopqrstuvwxyz','owner_public_request'),
            ('client_secret=correcthorsebatterystaple','owner_public_request'),
            ('secret: correcthorsebatterystaple','owner_public_request'),
            ('Basic dXNlcjpwYXNz','owner_public_request'),
            ('client_secret is correcthorsebatterystaple','owner_public_request'),
            ('secret is correcthorsebatterystaple','owner_public_request'),
            ('password requirements correcthorsebatterystaple','owner_public_request'),
            ('access token scopes actualsecretvalue','owner_public_request'),
            ('password, correcthorsebatterystaple','owner_public_request'),
            ('client secret correcthorsebatterystaple','owner_public_request'),
            ('client secret equals correcthorsebatterystaple','owner_public_request'),
            ('secret correcthorsebatterystaple','owner_public_request'),
            ('secret equals correcthorsebatterystaple','owner_public_request'),
            ('password requirements 123456789','owner_public_request'),
            ('password requirements 올바른비밀번호','owner_public_request'),
            ('Authorization: Token abcdefghijklmnop','owner_public_request'),
            ('Authorization: AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE','owner_public_request'),
            ('compare /root/.ssh/id_rsa','owner_public_request'),
            ('compare ~/Documents/tax-return.txt','owner_public_request'),
            (r'compare C:\private\receipt.txt','owner_public_request'),
            ('compare "/root/.ssh/id_rsa"','owner_public_request'),
            ('compare (/home/alice/tax.pdf)','owner_public_request'),
            (r'compare "C:\private\receipt.txt"','owner_public_request'),
            ('compare `/root/.ssh/id_rsa`','owner_public_request'),
            ('compare [/home/alice/tax.pdf]','owner_public_request'),
            ('file=/root/.ssh/id_rsa','owner_public_request'),
            ('compare C:/private/receipt.txt','owner_public_request'),
            ('compare [C:/private/receipt.txt]','owner_public_request'),
            ('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature','owner_public_request'),
            ('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M','owner_public_request'),
            (r'compare \\server\private\receipt.txt','owner_public_request'),
            ('compare //server/private/receipt.txt','owner_public_request'),
            ('compare ./secrets.txt','owner_public_request'),
            ('compare ../secrets.txt','owner_public_request'),
            ('compare /private','owner_public_request'),
            ('path:/root/.ssh/id_rsa','owner_public_request'),
            ('file:/root/.ssh/id_rsa','owner_public_request'),
            ('source:/home/alice/tax.pdf','owner_public_request'),
            ('file: secrets.txt','owner_public_request'),
            ('path: Documents/tax-return.pdf','owner_public_request'),
            ('eyJ9.e30.x','owner_public_request'),
            ('file= secrets.txt','owner_public_request'),
            ('path is Documents/tax-return.pdf','owner_public_request'),
            ('source, private-notes.md','owner_public_request'),
            ('api\u200b_key=actualsecretvalue','owner_public_request'),
            (r'compare \\\server\private\receipt.txt','owner_public_request'),
            (r'compare \/server\private/receipt.txt','owner_public_request'),
            ('-----BEGIN PRIVATE KEY----- abcdef -----END PRIVATE KEY-----','owner_public_request'),
            ('compare https://alice:supersecret@example.com/private','owner_public_request'),
            ('compare https://:supersecret@example.com/private','owner_public_request'),
            ('compare https://alice%3Asupersecret@example.com/private','owner_public_request'),
            ('eyJ9.e30.','owner_public_request'),
            ('file; secrets.txt','owner_public_request'),
            ('path as Documents/tax-return.pdf','owner_public_request'),
            ('source -> private-notes.md','owner_public_request'),
            ('-----BEGIN ENCRYPTED PRIVATE KEY----- abcdef','owner_public_request'),
            ('-----BEGIN PGP PRIVATE KEY BLOCK----- abcdef','owner_public_request'),
            ('AWS_SECRET_ACCESS_KEY=supersecret','owner_public_request'),
            ('DATABASE_PASSWORD=supersecret','owner_public_request'),
            ('DJANGO_SECRET_KEY=supersecret123456789','owner_public_request'),
            ('api%25E2%2580%258B_key=supersecret123456789','owner_public_request'),
            ('Cookie: sessionid=supersecret','owner_public_request'),
            ('https://example.com/?sessionid=supersecret123456789','owner_public_request'),
            ('JSESSIONID=supersecret123456789','owner_public_request'),
            ('csrftoken=supersecret123456789','owner_public_request'),
            ('PHPSESSID=supersecret123456789','owner_public_request'),
            ('https://example.com/?connect.sid=supersecret123456789','owner_public_request'),
            ('ASP.NET_SessionId=supersecret123456789','owner_public_request'),
            ('laravel_session=supersecret123456789','owner_public_request'),
            ('.AspNetCore.Session=supersecret123456789','owner_public_request'),
            ('https://example.com/?%2EAspNetCore%2ESession=supersecret123456789','owner_public_request'),
            ('$HOME/.ssh/id_rsa','owner_public_request'),
            ('${HOME}/Documents/private.txt','owner_public_request'),
            ('Basic dTpw','owner_public_request'),
            ('Basic dTo=','owner_public_request'),
            ('PGPASSWORD=hunter2value','owner_public_request'),
            ('token=supersecret123456789','owner_public_request'),
            ('compare https://example.com/?token=supersecret123456789','owner_public_request'),
            ('compare https://example.com/?%74oken=supersecret123456789','owner_public_request'),
            ('compare https://example.com/?token%3Dsupersecret123456789','owner_public_request'),
            ('Bearer abcdefghijklmnop','owner_public_request'),
            ('bEaReR AbCdEfGhIjKlMnOp','owner_public_request'),
        ]
        for query,source in cases:
            with self.subTest(query=query),self.assertRaises(ValueError):
                validate_public_query(query,source)

    def test_ordinary_public_credential_topics_are_not_overblocked(self):
        for query in ('password manager comparison','api key security best practices',
                      'access token documentation','refresh token rotation guide','password requirements',
                      'api key permissions','api key examples','access token scopes','refresh token revocation',
                      'client secret rotation guide','secret management best practices',
                      'session cookie security best practices',
                      'compare password requirements and api key permissions',
                      'authorization header format','compare https://example.com/public/path',
                      'compare "https://example.com/public/path"','JWT format examples',
                      'basic authentication overview','Basic Authentication overview',
                      'bearer authentication examples','Bearer authorization examples',
                      'basic economy fare comparison','basic room rates Seoul'):
            with self.subTest(query=query):
                self.assertEqual(validate_public_query(query,'owner_public_request'),query)
        for query in ('Secret Garden hotel Seoul','secret beach hotels Bali'):
            with self.subTest(query=query):
                self.assertEqual(validate_public_query(query,'owner_public_request'),query)

    def test_query_validation_requires_explicit_public_provenance(self):
        self.assertEqual(validate_public_query('  public hotels Seoul  ','owner_public_request'),'public hotels Seoul')
        with self.assertRaisesRegex(ValueError,'Memory'):
            validate_public_query('public hotels Seoul',None)

    def test_label_separators_behave_identically_on_every_label_path(self):
        """`secret; x` and `secret -> x` leaked while `password; x` was blocked.

        The label paths (high-confidence assignment, header-shaped labels,
        label assignment and the label-occurrence allowlist) must share one
        separator definition, so no separator may block one label while
        passing another.

        The header-shaped labels are included because they kept private
        separator sets after the first unification: `authorization`/`cookie`/
        `set-cookie`/`bearer` accepted only ':' and `phpsessid`/`sessionid`
        only '='/'=>', which allowed `Cookie; PHPSESSID abc123def456` - the
        original defect class verbatim.

        The SCREAMING_SNAKE environment-variable labels are included because
        they kept a private `\\s*=\\s*` set after the second unification:
        `PGPASSWORD=hunter2` was blocked while `PGPASSWORD: hunter2`,
        `MY_API_KEY: ...` and `DB_PASSWORD -> ...` were sent to the search
        provider. They cannot fall through to LABEL_ASSIGNMENT because the
        underscore prefix removes the word boundary `\\bapi[_ -]?key\\b`
        needs, so they must carry LABEL_SEPARATOR themselves.
        """
        separators=(':','=',',',';','|','->','=>',' is ',' equals ',' as ')
        labels=('password','secret','client secret','api key','credentials','token','source','path','file',
                'authorization','cookie','set-cookie','bearer','phpsessid','sessionid',
                'MY_API_KEY','DB_PASSWORD','APP_CLIENT_SECRET','STRIPE_TOKEN','SERVICE_ACCESS_KEY',
                'X_API_KEY','APP_PRIVATE_KEY','SVC_PASSWD','A_SECRET_KEY',
                'PGPASSWORD','MYSQL_PWD','REDISCLI_AUTH')
        def rejects(query):
            try:
                validate_public_query(query,'owner_public_request');return False
            except ValueError: return True
        blocking={label:frozenset(separator for separator in separators
                                  if rejects(f'{label}{separator}private-notes.md'))
                  for label in labels}
        self.assertEqual(len(set(blocking.values())),1,blocking)
        self.assertEqual(set(next(iter(blocking.values()))),set(separators))

    def test_header_shaped_labels_block_every_shared_separator(self):
        """`Cookie; PHPSESSID abc123def456` was allowed while `Cookie: ...` was not."""
        for query in ('Cookie; PHPSESSID abc123def456','cookie -> sess123abcdef',
                      'set-cookie, sid=abcdef123456','authorization => abcdef123456',
                      'bearer | abcdef123456','sessionid as abcdef123456',
                      'phpsessid is abcdef123456','token; abcdef123456'):
            with self.subTest(query=query):
                with self.assertRaisesRegex(ValueError,'자격 증명'):
                    validate_public_query(query,'owner_public_request')

    def test_basic_auth_credentials_block_every_shared_separator(self):
        """`basic dXNlcjpwYXNz` was blocked; every other separator was not.

        Found by sweeping the label -> value paths for the ND-3 defect class:
        the allowlist-aware Basic/Bearer scanners inside
        `validate_public_query` kept a bare `\\s+` instead of the shared
        separator, so base64 `user:pass` reached the search provider under
        ':', ';', ',', '->', '=>', '|', ' is ' and ' as '.
        """
        for separator in (' ',': ','; ',', ','-> ','=> ','| ',' is ',' as '):
            with self.subTest(separator=separator):
                with self.assertRaisesRegex(ValueError,'자격 증명'):
                    validate_public_query(f'basic{separator}dXNlcjpwYXNz','owner_public_request')
        # The allowlist must still pass ordinary public queries.
        for query in ('basic room rates Seoul','basic economy fare comparison'):
            with self.subTest(query=query):
                self.assertEqual(validate_public_query(query,'owner_public_request'),query)

    def test_environment_variable_suffix_vocabulary_has_no_terminal_escape(self):
        """The suffix list is a vocabulary, so it is where this class hid last.

        Three "unifications" on this path were each partial. After the
        separator sets were shared, `DB_PWD`, `APP_PASS` and `SSH_KEY` - all
        plausible real env-var names - were still absent from the suffix
        vocabulary, and a terminal plural or version digit (`MY_API_KEYS`,
        `MY_API_KEY2`) defeated a match anchored on the bare suffix. Every
        shape below reached the search provider before this test existed.
        """
        leaking = (
            "DB_PWD: hunter2",
            "APP_PASS: hunter2",
            "SSH_KEY: abcdef123456",
            "VAULT_AUTH: abcdef123456",
            "AWS_CREDENTIALS: abcdef123456",
            "APP_APIKEY: abcdef123456",
            "MY_SECRETKEY: abcdef123456",
            "MY_API_KEYS: abcdef123456",
            "DB_PASSWORDS: hunter2",
            "APP_TOKENS: abcdef123456",
            "MY_API_KEY2: abcdef123456",
            "MY_API_KEY_V2: abcdef123456",
            "MY.API.KEY: abcdef123456",
        )
        for query in leaking:
            with self.subTest(query=query):
                with self.assertRaisesRegex(ValueError, '자격 증명'):
                    validate_public_query(query, "owner_public_request")

    def test_capitalised_public_queries_are_not_mistaken_for_env_variables(self):
        """Widening the suffix vocabulary must not swallow ordinary queries.

        `PASS`, `KEY` and `AUTH` are common words, so the guard has to stay
        anchored on the underscore/dot-joined SCREAMING_SNAKE shape rather
        than on the word alone.
        """
        allowed = (
            "THE BEST HOTELS IN SEOUL",
            "MUSEUM PASS PRICE",
            "NEW YORK CITY GUIDE",
            "JR PASS comparison",
            "I-PASS toll road",
            "API design best practices",
            "Secret Garden hotel Seoul",
            "basic room rates Seoul",
        )
        for query in allowed:
            with self.subTest(query=query):
                validate_public_query(query, "owner_public_request")

    def test_environment_variable_secrets_block_every_shared_separator(self):
        """`PGPASSWORD=hunter2` was blocked while `PGPASSWORD: hunter2` was not.

        The two SCREAMING_SNAKE label paths kept a private `\\s*=\\s*` after
        the shared separator was introduced, so every non-`=` separator sent
        the credential to the search provider. These labels also cannot fall
        back to `LABEL_ASSIGNMENT`: the underscore prefix destroys the
        `\\bapi[_ -]?key\\b` word boundary, so nothing else catches them.
        """
        for query in ('PGPASSWORD=hunter2','PGPASSWORD: hunter2','PGPASSWORD; hunter2',
                      'PGPASSWORD -> hunter2','MY_API_KEY: abcdef123456',
                      'DB_PASSWORD -> abcdef123456','APP_CLIENT_SECRET; abcdef123456',
                      'STRIPE_TOKEN | abcdef123456','SERVICE_ACCESS_KEY as abcdef123456',
                      'MYSQL_PWD: hunter2','REDISCLI_AUTH, hunter2',
                      'X_API_KEY => abcdef123456','APP_PRIVATE_KEY is abcdef123456'):
            with self.subTest(query=query):
                with self.assertRaisesRegex(ValueError,'자격 증명'):
                    validate_public_query(query,'owner_public_request')
        # Ordinary public queries must survive the widened separator set.
        for query in ('Secret Garden hotel Seoul','hotel price comparison 2026',
                      'best rail pass Japan'):
            with self.subTest(query=query):
                self.assertEqual(validate_public_query(query,'owner_public_request'),query)

    def test_credential_and_identity_synonyms_are_not_sent_to_the_search_provider(self):
        for query in ('auth token abcdefghijklmnop','auth_token=abcdef123456',
                      'passphrase: correct horse battery','seed phrase: witch collapse practice',
                      'mnemonic: abandon abandon ability','pin: 4821','otp = 903214',
                      'credentials: hunter2value','cvv: 311','one-time code: 220913',
                      'passport number: M12345678','national id: 900101-1234567',
                      'card number: 4111 1111 1111 1111','4111111111111111',
                      '900101-1234567','123-45-6789','ssn is 123-45-6789'):
            with self.subTest(query=query),self.assertRaises(ValueError):
                validate_public_query(query,'owner_public_request')
        # The secondary tripwire must not swallow ordinary public queries.
        for query in ('Secret Garden hotel Seoul','bowling pin price comparison',
                      'otp authentication overview','secret management best practices'):
            with self.subTest(query=query):
                self.assertEqual(validate_public_query(query,'owner_public_request'),query)


if __name__ == '__main__':
    unittest.main()
