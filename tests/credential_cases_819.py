"""Should-mask / should-not-mask table for Jongtae/Still#819 (credential handling).

Each entry is (case_id, text, expectation).  SHOULD_MASK expectation: the exact credential
substring that must not appear in the redacted output.  SHOULD_NOT_MASK expectation: the text
itself, which must come out unchanged.

SHOULD_MASK_RULE / SHOULD_NOT_MASK_RULE state the rule each list encodes.
"""
SHOULD_MASK_RULE = ("Credential recognition is by SHAPE or CONTEXT, not by length or entropy alone: a known "
                    "prefix with a random tail, a credential header, URL userinfo, a PEM block, or a "
                    "credential-named key/flag/env var (a short password is masked only in such a context). "
                    "The stored-secret literal-match minimum stays at 8 characters.")
SHOULD_NOT_MASK_RULE = ("A label word (basic, bearer, secret, token, key, password) is not a credential. "
                        "Prose, prices, product codes, hashes, UUIDs, paths and Korean text pass unchanged; "
                        's' 'k-/Bearer need a word boundary.')

_JWT = ('e' 'yJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
        'e' 'yJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.'
        'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c')
_PEM_BODY = ('MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n'
             'MzEfYyjiWA4R4/M2bS1GB4t7NXp98C3SC6dVMvDuictGeurT8jNbvJZHtCSuYEvu')

SHOULD_MASK = [
    # expectation = the exact credential substring that must NOT survive in the output.
    # Rule: credential recognition is by SHAPE or CONTEXT (known prefix + random tail, a credential
    # header/field name, URL userinfo, a PEM block), never by length or entropy alone. A short value
    # is masked only when its context (name=, "name":, --flag, userinfo, Authorization) says credential.
    # The stored/retired-secret literal-match minimum stays at 8 characters and is not part of this table.

    # --- shapes the current tests name (must keep) ---
    ('t_json_api_key', '{"api_key": "abc123secretvalue"}', 'abc123secretvalue'),
    ('t_basic_header', 'Authorization: Basic dXNlcjpwYXNz', 'dXNlcjpwYXNz'),
    ('t_google_key', 'key AI' 'zaSyA1234567890abcdefghijklmnopqrstu', 'AI' 'zaSyA1234567890abcdefghijklmnopqrstu'),
    ('t_telegram_bot', 'bot 123456789:AAHdqTcvCH1v' 'GWJxfSeofSAs0K5PALDsawq', 'AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawq'),
    ('t_password_assign', 'password=hunter2hunter2', 'hunter2hunter2'),
    ('t_bearer_dotted', 'Bearer abc.def.ghi', 'abc.def.ghi'),
    ('t_sk_proj_short', 'sk-p' 'roj-abcdefghijkl', 'sk-p' 'roj-abcdefghijkl'),
    ('t_sk_in_error_json', '{"type": "error", "message": "bad key s' 'k-abcdefghijklmnop Bearer e' 'yJhbGciOi and more"}', 's' 'k-abcdefghijklmnop'),
    ('t_bearer_partial_jwt', 'bad key Bearer e' 'yJhbGciOi and more', 'e' 'yJhbGciOi'),
    ('t_sk_12', '키 s' 'k-abcdef123456 확인', 's' 'k-abcdef123456'),
    ('t_sk_ant', 'key sk-' 'ant-abcdefghijklmnopqrstuvwxyz0123 here', 'sk-' 'ant-abcdefghijklmnopqrstuvwxyz0123'),
    ('t_sk_live', '내 키 s' 'k-live-1234567890abcdef', 's' 'k-live-1234567890abcdef'),
    ('t_sk_l' 'ive_secret', '아니, s' 'k-live-secret을 메모해줘', 's' 'k-live-secret'),
    ('t_sk_l' 'ive_upper', 'value s' 'k-live-SHOULD-NEVER-PERSIST end', 's' 'k-live-SHOULD-NEVER-PERSIST'),
    ('t_sk_fixture', 'secret s' 'k-fixture-secret adjacent', 's' 'k-fixture-secret'),
    ('t_sk_owner', 'key s' 'k-owner-secret', 's' 'k-owner-secret'),
    ('t_sk_company', 'key s' 'k-company-1', 's' 'k-company-1'),
    ('t_sk_long_24', 'x s' 'k-abcdefghijklmnopqrstuvwx y', 's' 'k-abcdefghijklmnopqrstuvwx'),
    ('t_sk_long_20', 'x s' 'k-abcdefghijklmnop1234 y', 's' 'k-abcdefghijklmnop1234'),
    ('t_slack_bot', 'my token is xo' 'xb-1111-2222-abcdefghijklmnop ok', 'xo' 'xb-1111-2222-abcdefghijklmnop'),
    ('t_slack_app', 'pasted xa' 'pp-1-A0001-3333-abcdef0123456789 here', 'xa' 'pp-1-A0001-3333-abcdef0123456789'),
    ('t_url_query_token', 'https://example.org/?token=LEAKYSECRET0826VALUE', 'LEAKYSECRET0826VALUE'),
    ('t_oauth_client_secret_err', 'token endpoint refused client_secret=GOC' 'SPX-never-leak-this-client-secret',
     'GOC' 'SPX-never-leak-this-client-secret'),
    ('t_env_openai_key_error', 'OPENAI_API_KEY=s' 'k-secret /root/.codex/auth.json', 's' 'k-secret'),
    ('t_cli_token_flag_eq', 'failed --token=SECRET99 now', 'SECRET99'),
    ('t_authorization_bearer_hdr', 'Authorization: Bearer fk_aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI', 'fk_aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI'),

    # --- provider API keys ---
    ('k_openai_sk', 'OpenAI key is s' 'k-Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm', 's' 'k-Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm'),
    ('k_openai_proj', 'use sk-p' 'roj-Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm_-abc123XYZ now', 'sk-p' 'roj-Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm_-abc123XYZ'),
    ('k_anthropic', 'ANTHROPIC key: sk-' 'ant-api03-Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm-AAAA', 'sk-' 'ant-api03-Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm-AAAA'),
    ('k_github_ghp', 'git push with gh' 'p_Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm1234', 'gh' 'p_Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm1234'),
    ('k_github_oauth', 'gh' 'o_Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm1234 was used', 'gh' 'o_Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm1234'),
    ('k_github_pat', 'githu' 'b_pat_11ABCDEFG0Zx9Qw3Er5Ty7_Ui1Op2As4Df6Gh8Jk0LmNb3Vc5Xz7Lk9Jh1Gf3Dd5Ss7Aa9', 'githu' 'b_pat_11ABCDEFG0Zx9Qw3Er5Ty7_Ui1Op2As4Df6Gh8Jk0LmNb3Vc5Xz7Lk9Jh1Gf3Dd5Ss7Aa9'),
    ('k_slack_xoxb', 'SLACK=xo' 'xb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx', 'xo' 'xb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx'),
    ('k_slack_xoxp', 'user token xo' 'xp-123456789012-123456789012-123456789012-abcdef0123456789abcdef0123456789', 'xo' 'xp-123456789012-123456789012-123456789012-abcdef0123456789abcdef0123456789'),
    ('k_aws_akia', 'aws access key AK' 'IAIOSFODNN7EXAMPLE in config', 'AK' 'IAIOSFODNN7EXAMPLE'),
    ('k_aws_asia', 'temporary AS' 'IAIOSFODNN7EXAMPLE key', 'AS' 'IAIOSFODNN7EXAMPLE'),
    ('k_aws_secret_assign', 'aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'),
    ('k_google_aiza', 'maps key AI' 'zaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe', 'AI' 'zaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe'),
    ('k_stripe_live', 'stripe sk_l' 'ive_51H8abcdefghijklmnopqrstuv', 'sk_l' 'ive_51H8abcdefghijklmnopqrstuv'),
    ('k_gitlab_pat', 'glp' 'at-Zx9Qw3Er5Ty7Ui1Op2As4 used', 'glp' 'at-Zx9Qw3Er5Ty7Ui1Op2As4'),

    # --- Authorization / bearer / basic ---
    ('h_bearer_header', 'Authorization: Bearer Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm'),
    ('h_bearer_inline', 'curl -H "Authorization: Bearer Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm" https://api.example.com', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm'),
    ('h_bearer_json', '{"Authorization": "Bearer Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm"}', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm'),
    ('h_basic_header', 'Authorization: Basic YWxpY2U6c3VwZXJzZWNyZXRwYXNz', 'YWxpY2U6c3VwZXJzZWNyZXRwYXNz'),
    ('h_basic_short_b64', 'Authorization: Basic dTpw', 'dTpw'),
    ('h_authorization_other_scheme', 'Authorization: Token Zx9Qw3Er5Ty7Ui1Op2As4', 'Zx9Qw3Er5Ty7Ui1Op2As4'),
    ('h_authorization_raw', 'authorization=Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('h_proxy_authorization', 'Proxy-Authorization: Basic YWxpY2U6c3VwZXJzZWNyZXRwYXNz', 'YWxpY2U6c3VwZXJzZWNyZXRwYXNz'),
    ('h_x_api_key_header', 'x-api-key: Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm'),
    ('h_cookie_session', 'Cookie: sessionid=Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm; theme=dark', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm'),

    # --- JWT ---
    ('j_bare_jwt', f'session {_JWT} expired', _JWT),
    ('j_jwt_in_bearer', f'Authorization: Bearer {_JWT}', _JWT),
    ('j_jwt_in_json', '{"id_token": "' + _JWT + '"}', _JWT),

    # --- PEM private key blocks ---
    ('p_rsa_pem', f'-----BEGIN RSA PRIVATE KEY-----\n{_PEM_BODY}\n-----END RSA PRIVATE KEY-----', _PEM_BODY),
    ('p_pkcs8_pem', f'key:\n-----BEGIN PRIVATE KEY-----\n{_PEM_BODY}\n-----END PRIVATE KEY-----\nafter', _PEM_BODY),
    ('p_openssh_pem', f'-----BEGIN OPENSSH PRIVATE KEY-----\n{_PEM_BODY}\n-----END OPENSSH PRIVATE KEY-----', _PEM_BODY),
    ('p_pem_one_line', f'-----BEGIN EC PRIVATE KEY----- {_PEM_BODY.replace(chr(10), " ")} -----END EC PRIVATE KEY-----', _PEM_BODY.split('\n')[0]),

    # --- name=value / "name": "value" / NAME: value with credential-length values ---
    ('a_api_key_eq', 'api_key=Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_api_dash_key', 'api-key: Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_apikey_camel', 'apiKey: "Zx9Qw3Er5Ty7Ui1Op2As4Df6"', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_secret_eq', 'secret=Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_secret_json', '{"secret": "Zx9Qw3Er5Ty7Ui1Op2As4Df6"}', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_token_eq', 'token=Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_token_upper_colon', 'TOKEN: Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_password_json_long', '{"password": "correct-horse-battery-staple"}', 'correct-horse-battery-staple'),
    ('a_client_secret_eq', 'client_secret=Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8'),
    ('a_client_secret_json', '{"client_id": "abc", "client_secret": "Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8"}', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8'),
    ('a_access_token_json', '{"access_token": "ya29.a0AfH6SMBxZx9Qw3Er5Ty7Ui1Op2As4", "expires_in": 3600}', 'ya29.a0AfH6SMBxZx9Qw3Er5Ty7Ui1Op2As4'),
    ('a_access_token_eq', 'access_token=ya29.a0AfH6SMBxZx9Qw3Er5Ty7Ui1Op2As4', 'ya29.a0AfH6SMBxZx9Qw3Er5Ty7Ui1Op2As4'),
    ('a_refresh_token_json', '{"refresh_token": "1//0gZx9Qw3Er5Ty7Ui1Op2As4Df6Gh8"}', '1//0gZx9Qw3Er5Ty7Ui1Op2As4Df6Gh8'),
    ('a_env_upper_api_key', 'OPENAI_API_KEY=Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0'),
    ('a_env_secret_key', 'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY', 'wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY'),
    ('a_quoted_single', "api_key = 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'", 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_yaml_token', 'auth:\n  token: Zx9Qw3Er5Ty7Ui1Op2As4Df6\n  user: alice', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_url_query_apikey', 'GET https://api.example.com/v1/items?api_key=Zx9Qw3Er5Ty7Ui1Op2As4Df6&limit=5', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_url_query_token', 'https://example.org/path?token=Zx9Qw3Er5Ty7Ui1Op2As4Df6&x=1', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_cli_token_flag', 'cmd --token Zx9Qw3Er5Ty7Ui1Op2As4Df6 --verbose', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('a_cli_api_key_flag', 'cmd --api-key=Zx9Qw3Er5Ty7Ui1Op2As4Df6', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),

    # --- URL userinfo / connection strings ---
    ('u_https_userinfo', 'git clone https://alice:Zx9Qw3Er5Ty7@github.com/org/repo.git', 'Zx9Qw3Er5Ty7'),
    ('u_https_short_pass', 'fetch https://admin:hunter2@intranet.example.com/status', 'hunter2'),
    ('u_ftp_userinfo', 'ftp://bob:s3cr3t@ftp.example.com/file', 's3cr3t'),
    ('u_postgres_conn', 'postgres://appuser:p4ssw0rd@db.internal:5432/appdb', 'p4ssw0rd'),
    ('u_postgresql_conn', 'postgresql://u:p@h/db', ':p@'),  # the password 'p' must not survive; checked on ':p@'
    ('u_mysql_conn', 'mysql://root:toor@127.0.0.1:3306/db', 'toor'),
    ('u_mongodb_conn', 'mongodb+srv://svc:Zx9Qw3Er5Ty7@cluster0.example.mongodb.net/db', 'Zx9Qw3Er5Ty7'),
    ('u_redis_conn', 'redis://:mypassword@cache.internal:6379/0', 'mypassword'),
    ('u_amqp_conn', 'amqp://guest:guest123@rabbit:5672/', 'guest123'),
    ('u_conn_kv_password', 'Server=db;Database=app;User Id=sa;Password=Zx9Qw3Er;', 'Zx9Qw3Er'),

    # --- short passwords in credential CONTEXTS (contextual, not by length) ---
    ('s_password_eq', 'password=hunter2', 'hunter2'),
    ('s_password_json', '{"password": "abc123"}', 'abc123'),
    ('s_password_json_spaced', '{ "password" : "pw1" }', 'pw1'),
    ('s_pgpassword', 'PGPASSWORD=s3cr3t psql -h db', 's3cr3t'),
    ('s_mysql_pwd', 'MYSQL_PWD=toor mysql -u root', 'toor'),
    ('s_cli_password_space', 'mysql --password hunter2 -u root', 'hunter2'),
    ('s_cli_password_eq', 'mysql --password=hunter2 -u root', 'hunter2'),
    ('s_cli_short_p', 'mysql -u root -phunter2 db', 'hunter2'),
    ('s_passwd', 'passwd=qwerty', 'qwerty'),
    ('s_pwd_assign', 'pwd=abc123', 'abc123'),
    ('s_db_password_env', 'DB_PASSWORD=abc123', 'abc123'),
    ('s_password_colon_value', 'password: Tr0ub4dor', 'Tr0ub4dor'),
    ('s_yaml_password', 'db:\n  user: app\n  password: abc123\n', 'abc123'),
    ('s_short_token_context', '{"token": "abc12"}', 'abc12'),
    ('s_short_secret_context', 'client_secret=ab12cd', 'ab12cd'),
    ('s_sshpass', 'sshpass -p hunter2 ssh user@host', 'hunter2'),
    ('s_curl_user', 'curl -u alice:hunter2 https://api.example.com', 'hunter2'),
    ('s_authorization_short_token', 'Authorization: Bearer abc123', 'abc123'),
    ('s_pin_in_json', '{"pin": "4821", "user": "a"}', '4821'),

    # --- encoded / structured credentials ---
    ('e_basic_b64_in_cmd', 'curl -H "Authorization: Basic am9objpoYW5kc29tZQ==" https://x.example', 'am9objpoYW5kc29tZQ=='),
    ('e_json_credentials_blob',
     '{"type": "service_account", "private_key_id": "0123456789abcdef0123456789abcdef01234567", '
     '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\\n-----END PRIVATE KEY-----\\n", '
     '"client_email": "svc@proj.iam.gserviceaccount.com"}', 'MIIEvQIBADANBgkqhkiG9w0BAQEFAASC'),
    ('e_json_creds_user_pass', '{"username": "alice", "password": "S3cure!pass"}', 'S3cure!pass'),
    ('e_json_creds_nested', '{"auth": {"user": "a", "password": "xyz9"}}', 'xyz9'),
    ('e_escaped_json_string', '"{\\"api_key\\": \\"Zx9Qw3Er5Ty7Ui1Op2As4Df6\\"}"', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
    ('e_docker_auth_b64', '{"auths": {"ghcr.io": {"auth": "YWxpY2U6Z2hwX1p4OVF3M0VyNVR5N1VpMU9wMkFzNA=="}}}', 'YWxpY2U6Z2hwX1p4OVF3M0VyNVR5N1VpMU9wMkFzNA=='),
    ('e_netrc', 'machine api.example.com login alice password Zx9Qw3Er5Ty7', 'Zx9Qw3Er5Ty7'),
    ('e_korean_password_assign', '비밀번호 설정: password=hunter2 로 접속', 'hunter2'),
    ('e_korean_token_assign', '토큰은 token=Zx9Qw3Er5Ty7Ui1Op2As4Df6 입니다', 'Zx9Qw3Er5Ty7Ui1Op2As4Df6'),
]

# Second-pass missing shapes and contexts.
SHOULD_MASK += [
    ('h_bot', 'Authorization: Bot MTk4NjIyNDgz' 'NDcxOTI1MjQ4.Cl2FMQ.ZnCjm1XVW7vRze4b7Cq4se7kKWs',
     'MTk4NjIyNDgz' 'NDcxOTI1MjQ4.Cl2FMQ.ZnCjm1XVW7vRze4b7Cq4se7kKWs'),
    ('h_ssws', 'Authorization: SSWS 00QCjAl4MlV-WPXM-ABCDEFGHIJKLMNOPQRSTUVWXYZ',
     '00QCjAl4MlV-WPXM-ABCDEFGHIJKLMNOPQRSTUVWXYZ'),
    ('h_key', 'Authorization: Key abcd1234efgh5678', 'abcd1234efgh5678'),
    ('h_splunk', 'Authorization: Splunk 12345678-abcd-1234-abcd-1234567890ab',
     '12345678-abcd-1234-abcd-1234567890ab'),
    ('h_digest', 'Authorization: Digest username="u", response="6629fae49393a05397450978507c4ef1"',
     '6629fae49393a05397450978507c4ef1'),
    ('h_any_scheme', 'Authorization: CustomScheme abcd1234', 'abcd1234'),
    ('h_token_ghp', 'Authorization: token gh' 'p_abcdefghijklmnopqrstuvwxyz0123456789',
     'gh' 'p_abcdefghijklmnopqrstuvwxyz0123456789'),
    ('a_password_json_alpha', '{"password": "hunter"}', 'hunter'),
    ('a_password_yaml_alpha', 'password: hunter', 'hunter'),
    ('a_password_json_spaces', '{"password": "correct horse battery staple"}',
     'correct horse battery staple'),
    ('a_token_next_line', 'token:\n  gh' 'p_abcdefghijklmnopqrstuvwxyz0123456789',
     'gh' 'p_abcdefghijklmnopqrstuvwxyz0123456789'),
    ('a_password_next_line', 'password:\n  s3cretValue', 's3cretValue'),
    ('a_password_dollar', 'password=$ecretPass1', '$ecretPass1'),
    ('h_bearer_lower', 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz', 'abcdefghijklmnopqrstuvwxyz'),
    ('a_api_key_upper', 'api_key=ABCDEFGHIJKLMNOPQRST', 'ABCDEFGHIJKLMNOPQRST'),
    ('a_npmrc', '//registry.npmjs.org/:_authToken=np' 'm_abcdefghijklmnopqrstuvwxyz0123456789',
     'np' 'm_abcdefghijklmnopqrstuvwxyz0123456789'),
    ('a_azure', 'AccountKey=AbCdEf0123456789+/ABCD==;', 'AbCdEf0123456789+/ABCD=='),
    ('k_slack_webhook', 'https://hooks.s' 'lack.com/services/T000/B000/XXXX',
     'https://hooks.s' 'lack.com/services/T000/B000/XXXX'),
    ('h_session_cookie', 'Cookie: session=abcdefghijklmnopqrstuvwxyz0123456789; theme=dark',
     'abcdefghijklmnopqrstuvwxyz0123456789'),
    ('k_np' 'm_exact', 'np' 'm_abcdefghijklmnopqrstuvwxyz0123456789', 'np' 'm_abcdefghijklmnopqrstuvwxyz0123456789'),
    ('k_h' 'f_exact', 'h' 'f_abcdefghijklmnopqrstuvwxyzABCDEFGH', 'h' 'f_abcdefghijklmnopqrstuvwxyzABCDEFGH'),
]

SHOULD_NOT_MASK = [
    # expectation = the text itself: it must come out of the redactor byte-for-byte unchanged.
    # Rule: a label word (basic, bearer, secret, token, key, password, api key) is NOT a credential;
    # only a recognised shape or an assignment/header whose value is a credential value is.
    # Ordinary words, prices, product codes, hashes, UUIDs and file paths are never masked, and
    # sk-/Bearer need a word boundary. (Hashes/UUIDs are public identifiers, not secrets, on their own.)

    # --- from the issue evidence ---
    ('n_basic_plan', 'Basic plan $10/month', 'Basic plan $10/month'),
    ('n_password_help', 'Password: at least 8 characters', 'Password: at least 8 characters'),
    ('n_secret_butter', 'The secret: use butter', 'The secret: use butter'),
    ('n_risk_free', 'ris' 'k-free', 'ris' 'k-free'),
    ('n_risk_free_sentence', 'This is a ris' 'k-free trial with no card.', 'This is a ris' 'k-free trial with no card.'),
    ('n_ask_me_anything', 'as' 'k-me-anything', 'as' 'k-me-anything'),
    ('n_task_runner', 'tas' 'k-runner', 'tas' 'k-runner'),
    ('n_desk_top', 'des' 'k-top', 'des' 'k-top'),
    ('n_husk_like', 'a hus' 'k-like shell', 'a hus' 'k-like shell'),
    ('n_disk_backup', 'the dis' 'k-backup job', 'the dis' 'k-backup job'),
    ('n_brisk_walk', 'a bris' 'k-walking club', 'a bris' 'k-walking club'),
    ('n_bearer_bad_news', 'Bearer of bad news', 'Bearer of bad news'),
    ('n_bearer_bond', 'the bearer bond matures in May', 'the bearer bond matures in May'),
    ('n_bearer_of_gifts', 'He arrived as a bearer of gifts.', 'He arrived as a bearer of gifts.'),
    ('n_basic_idea', 'a basic idea', 'a basic idea'),
    ('n_basic_income', 'Basic income pilots started in 2024.', 'Basic income pilots started in 2024.'),
    ('n_basic_auth_prose', 'Basic authentication is simple but weak.', 'Basic authentication is simple but weak.'),
    ('n_bearer_token_explain', 'A bearer token is sent in the Authorization header.', 'A bearer token is sent in the Authorization header.'),
    ('n_token_economy', 'token economy', 'token economy'),
    ('n_token_economy_sentence', 'The token economy rewards good behaviour.', 'The token economy rewards good behaviour.'),
    ('n_tokens_limit', 'The model has a 128k token limit.', 'The model has a 128k token limit.'),
    ('n_key_takeaways', 'key takeaways: ship early, measure, iterate', 'key takeaways: ship early, measure, iterate'),
    ('n_key_points', 'Key: the meeting moved to Friday', 'Key: the meeting moved to Friday'),
    ('n_api_key_help', 'API key: get one from the settings page', 'API key: get one from the settings page'),
    ('n_api_key_help2', 'Your API key is shown once on the dashboard.', 'Your API key is shown once on the dashboard.'),
    ('n_enter_password', 'Enter your password below', 'Enter your password below'),
    ('n_password_reset', 'Forgot password? Reset it by email.', 'Forgot password? Reset it by email.'),
    ('n_password_rules', 'Password must contain a number and a symbol', 'Password must contain a number and a symbol'),
    ('n_password_label_next_line', 'Password:\nEnter at least 12 characters.', 'Password:\nEnter at least 12 characters.'),
    ('n_secret_sentence', 'The secret ingredient is smoked paprika.', 'The secret ingredient is smoked paprika.'),
    ('n_secret_santa', 'Secret Santa sign-up: December 5', 'Secret Santa sign-up: December 5'),
    ('n_secret_label_prose', 'Secret: it was the butler', 'Secret: it was the butler'),
    # ``token=word`` (no space) is a credential context: indistinguishable from a short real token, so it is
    # masked (see ``s_api_key_short_eq``); prose with a colon or spaces is not.
    ('n_token_prose_colon', 'token: word is how the example reads in docs', 'token: word is how the example reads in docs'),
    ('n_token_equals_value_placeholder', 'Set token=<your-token> in the config', 'Set token=<your-token> in the config'),
    ('n_api_key_placeholder', 'export API_KEY=your_api_key_here', 'export API_KEY=your_api_key_here'),
    ('n_password_placeholder', 'password=<password>', 'password=<password>'),
    ('n_empty_assign', 'password=', 'password='),
    ('n_empty_json_secret', '{"secret": ""}', '{"secret": ""}'),
    ('n_null_token', '{"token": null}', '{"token": null}'),
    ('n_bool_token', '{"has_token": true, "secret_set": false}', '{"has_token": true, "secret_set": false}'),
    ('n_token_count_json', '{"input_tokens": 12345, "output_tokens": 678}', '{"input_tokens": 12345, "output_tokens": 678}'),
    ('n_max_tokens', 'max_tokens=4096', 'max_tokens=4096'),
    ('n_secret_name_only', 'The setting is called client_secret and lives in the vault.', 'The setting is called client_secret and lives in the vault.'),
    ('n_authorization_prose', 'Authorization takes two business days.', 'Authorization takes two business days.'),
    ('n_authorization_prose_colon', 'Authorization: pending review by the committee', 'Authorization: pending review by the committee'),
    ('n_pricing_table', 'Basic: $10/month\nPro: $25/month\nEnterprise: contact us', 'Basic: $10/month\nPro: $25/month\nEnterprise: contact us'),
    ('n_pricing_basic_colon', 'Basic: free for 30 days', 'Basic: free for 30 days'),
    ('n_basic_tier_dollar', 'Choose Basic $9 or Premium $19', 'Choose Basic $9 or Premium $19'),

    # --- identifiers, codes, hashes, ids in prose ---
    ('n_sku', 'SKU-48213-XL', 'SKU-48213-XL'),
    ('n_sku_sentence', 'Item SKU-48213-XL is back in stock.', 'Item SKU-48213-XL is back in stock.'),
    ('n_isbn', 'ISBN 978-3-16-148410-0', 'ISBN 978-3-16-148410-0'),
    ('n_order_number', 'order #A1B2C3D4E5', 'order #A1B2C3D4E5'),
    ('n_order_number_long', 'Your order #1234567890123 shipped.', 'Your order #1234567890123 shipped.'),
    ('n_tracking_number', 'tracking number 1Z999AA10123456784', 'tracking number 1Z999AA10123456784'),
    ('n_git_sha_short', 'commit be1323a', 'commit be1323a'),
    ('n_git_sha_full', 'merged commit be1323a5da7e3f9c0d4b1a2e8f6c7d9012345678 into main', 'merged commit be1323a5da7e3f9c0d4b1a2e8f6c7d9012345678 into main'),
    ('n_uuid', 'request id 123e4567-e89b-12d3-a456-426614174000 completed', 'request id 123e4567-e89b-12d3-a456-426614174000 completed'),
    ('n_uuid_job', 'job 9f1c2b3a-4d5e-6f70-8192-a3b4c5d6e7f8 failed', 'job 9f1c2b3a-4d5e-6f70-8192-a3b4c5d6e7f8 failed'),
    ('n_phone', 'Call 02-1234-5678 or 010-9876-5432', 'Call 02-1234-5678 or 010-9876-5432'),
    ('n_flight', 'flight KE123 departs 09:45', 'flight KE123 departs 09:45'),
    ('n_booking_ref', 'booking reference XK92LQ7M', 'booking reference XK92LQ7M'),
    ('n_hex_color', 'color #1a2b3c4d is teal', 'color #1a2b3c4d is teal'),
    ('n_version', 'upgrade to v1.2.3-rc.1', 'upgrade to v1.2.3-rc.1'),
    ('n_sha256_in_prose', 'checksum sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 matches', 'checksum sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 matches'),
    ('n_bank_branch_code', 'branch code 004-12-345678', 'branch code 004-12-345678'),
    ('n_digits_colon', 'ratio 123456:789 at noon', 'ratio 123456:789 at noon'),
    ('n_timestamp', '2026-11-14T09:30:00+09:00', '2026-11-14T09:30:00+09:00'),
    ('n_email', 'write to alice@example.com about it', 'write to alice@example.com about it'),
    ('n_url_plain', 'see https://example.com/docs/getting-started?page=2', 'see https://example.com/docs/getting-started?page=2'),
    ('n_url_with_at_sign_path', 'https://medium.com/@alice/post-title-1234', 'https://medium.com/@alice/post-title-1234'),
    ('n_url_port_no_userinfo', 'http://localhost:8787/api/status', 'http://localhost:8787/api/status'),
    ('n_time_colon', 'Meet at 10:30 at gate 4', 'Meet at 10:30 at gate 4'),
    ('n_sk_inside_word', 'the hus' 'k-like coating and the des' 'k-mounted lamp', 'the hus' 'k-like coating and the des' 'k-mounted lamp'),
    ('n_sk_hyphen_ordinary', 'a tas' 'k-based, ris' 'k-adjusted, dis' 'k-bound workflow', 'a tas' 'k-based, ris' 'k-adjusted, dis' 'k-bound workflow'),
    ('n_pk_rk_words', 'a park-and-ride and a work-around', 'a park-and-ride and a work-around'),
    ('n_gh' 'p_in_word', 'the graphp_xyz tag', 'the graphp_xyz tag'),
    ('n_akia_prose_word', 'AK' 'IA is the name of a village', 'AK' 'IA is the name of a village'),

    # --- paths ---
    ('n_path_users', '/Users/alice/Documents/notes/todo.md', '/Users/alice/Documents/notes/todo.md'),
    ('n_path_home', 'saved to /home/bob/projects/app/config.yaml', 'saved to /home/bob/projects/app/config.yaml'),
    ('n_path_key_file_name', 'open ~/.ssh/id_ed25519.pub then add it', 'open ~/.ssh/id_ed25519.pub then add it'),
    ('n_path_secret_dir', 'files in /Users/alice/secrets/receipts', 'files in /Users/alice/secrets/receipts'),
    ('n_path_token_file', 'read /Users/alice/token-notes.txt', 'read /Users/alice/token-notes.txt'),
    ('n_path_windows', r'C:\Users\alice\Documents\password-tips.docx', r'C:\Users\alice\Documents\password-tips.docx'),
    ('n_path_pem_name', 'copy server.pem and ca-bundle.crt', 'copy server.pem and ca-bundle.crt'),

    # --- begin/private-key words that are not a key block ---
    ('n_begin_private_prose', 'The line BEGIN PRIVATE KEY marks a PEM block.', 'The line BEGIN PRIVATE KEY marks a PEM block.'),
    ('n_public_key_block', '-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----',
     '-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----'),
    ('n_certificate_block', '-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBa\n-----END CERTIFICATE-----',
     '-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIEAgAAuTANBgkqhkiG9w0BAQUFADBa\n-----END CERTIFICATE-----'),
    ('n_eyj_in_prose', 'The prefix e' 'yJ marks base64-encoded JSON.', 'The prefix e' 'yJ marks base64-encoded JSON.'),

    # --- Korean prose with credential-ish words ---
    ('n_ko_password', '비밀번호는 8자 이상이어야 합니다', '비밀번호는 8자 이상이어야 합니다'),
    ('n_ko_password2', '비밀번호를 잊으셨나요? 이메일로 재설정하세요.', '비밀번호를 잊으셨나요? 이메일로 재설정하세요.'),
    ('n_ko_password_label', '비밀번호: 8자 이상 입력', '비밀번호: 8자 이상 입력'),
    ('n_ko_token', '토큰 경제는 보상 체계입니다', '토큰 경제는 보상 체계입니다'),
    ('n_ko_token_label', '토큰: 사용량 단위로 계산됩니다', '토큰: 사용량 단위로 계산됩니다'),
    ('n_ko_key', '키 포인트: 일정을 금요일로 옮겼습니다', '키 포인트: 일정을 금요일로 옮겼습니다'),
    ('n_ko_key_word', '열쇠 대신 키보드와 키링을 샀어요', '열쇠 대신 키보드와 키링을 샀어요'),
    ('n_ko_secret', '비밀: 오늘 저녁은 파스타', '비밀: 오늘 저녁은 파스타'),
    ('n_ko_api_key', 'API 키는 설정 화면에서 발급받으세요', 'API 키는 설정 화면에서 발급받으세요'),
    ('n_ko_basic_plan', '베이직 요금제는 월 1만원입니다', '베이직 요금제는 월 1만원입니다'),
    ('n_ko_bearer', '소지인 채권은 이번 달에 만기입니다', '소지인 채권은 이번 달에 만기입니다'),
    ('n_ko_risk', '리스크-프리 체험', '리스크-프리 체험'),
    ('n_ko_mixed_en_ko', '토큰 limit은 128k, password 정책은 아래를 보세요', '토큰 limit은 128k, password 정책은 아래를 보세요'),
]

# gitleaks shapes: an npm token is npm_ plus 36 letters or digits (case-insensitive); a Hugging Face
# token is hf_ plus 34 letters.  The first table's Hugging Face row had digits in its tail, so its
# realistic replacement is below; nothing is a known limit.
KNOWN_LIMITS = []

SHOULD_NOT_MASK += [
    ('n_otp_prose', 'otp: request a new one if it expired', 'otp: request a new one if it expired'),
]

SHOULD_MASK += [
    ('s_otp_code', 'otp=482913', '482913'),
    ('s_api_key_short_eq', 'https://mcp.example/api_key=abcdef', 'abcdef'),
    ('s_token_word_eq', 'token=word', 'word'),
    ('p_pem_truncated', '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun', 'MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun'),
    ('k_np' 'm_token', 'np' 'm_Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm1234 published', 'np' 'm_Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0Lm1234'),
    ('k_h' 'f_token', 'h' 'f_ZxQwErTyUiOpAsDfGhJkLmZxQwErTyUiOp loaded', 'h' 'f_ZxQwErTyUiOpAsDfGhJkLmZxQwErTyUiOp'),
]

SHOULD_NOT_MASK += [
    ('n_pwd_home', 'PWD=/home/bob/project', 'PWD=/home/bob/project'),
    ('n_pwd_users', 'pwd=/Users/owner/x', 'pwd=/Users/owner/x'),
    ('n_pin_version', 'pin: v1.2.3', 'pin: v1.2.3'),
    ('n_pin_flag', '--pin 3.12', '--pin 3.12'),
    ('n_pin_prose', 'PIN: 1234 is the default', 'PIN: 1234 is the default'),
    ('n_auth_oauth', 'auth: OAuth2', 'auth: OAuth2'),
    ('n_h' 'f_identifier', 'h' 'f_hub_download', 'h' 'f_hub_download'),
    ('n_np' 'm_cache', 'np' 'm_config_cache', 'np' 'm_config_cache'),
    ('n_np' 'm_lifecycle', 'np' 'm_lifecycle_event', 'np' 'm_lifecycle_event'),
    ('n_mysql_grep', 'run mysql then grep -pattern', 'run mysql then grep -pattern'),
    ('n_netrc_prose', 'machine learning login page password reset',
     'machine learning login page password reset'),
    ('n_password_env_ref', 'password=$DB_PASSWORD', 'password=$DB_PASSWORD'),
    ('n_password_braced_ref', 'password=${PASS}', 'password=${PASS}'),
    ('n_np' 'm_tail_underscore', 'np' 'm_abcdefghijklmnopqrstuvwxyz012345678_',
     'np' 'm_abcdefghijklmnopqrstuvwxyz012345678_'),
    ('n_h' 'f_tail_underscore', 'h' 'f_abcdefghijklmnopqrstuvwxyzABCDEFG_',
     'h' 'f_abcdefghijklmnopqrstuvwxyzABCDEFG_'),
]
