import unittest
from personal_agent.providers import ModelAdapter, ProviderError, validate_model

CFG = {'provider':'openai','endpoint':'https://api.openai.com/v1','model':'gpt-6.1-terra','api':'responses'}
TOOLS = [{'type':'function','function':{'name':'probe','description':'Check','parameters':{'type':'object','properties':{}}}}]

class ResponsesTest(unittest.TestCase):
    def test_config_opt_in(self):
        self.assertEqual(validate_model(CFG)['api'], 'responses')
        with self.assertRaises(ValueError):
            validate_model({**CFG,'provider':'compatible'})
        with self.assertRaises(ValueError):
            validate_model({**CFG,'api':'typo'})

    def test_tool_roundtrip(self):
        sent=[]
        output=[{'type':'reasoning','id':'rs_1','summary':[], 'encrypted_content':'opaque'},
                {'type':'function_call','id':'fc_1','call_id':'call_1','name':'probe','arguments':'{}'}]
        def transport(url, body, headers, timeout=60):
            sent.append((url,body,timeout))
            return {'status':'completed','model':'observed-terra','output':output if len(sent)==1 else [
                {'type':'message','role':'assistant','content':[{'type':'output_text','text':'Done'}]}]}
        adapter=ModelAdapter(transport)
        history=[{'role':'user','content':'Check'}]
        message, model=adapter.tool_turn(CFG,'key',history,TOOLS,tool_choice='required',timeout=20,report_observed=True)
        self.assertEqual(model,'observed-terra')
        self.assertEqual(message['tool_calls'][0]['id'],'call_1')
        self.assertEqual(sent[0][0],CFG['endpoint']+'/responses')
        self.assertFalse(sent[0][1]['store'])
        self.assertFalse(sent[0][1]['tools'][0]['strict'])
        self.assertEqual(sent[0][2],20)
        history += [message,{'role':'tool','tool_call_id':'call_1','content':'ok'}]
        reply,_=adapter.tool_turn(CFG,'key',history,TOOLS)
        self.assertEqual(reply['content'],'Done')
        self.assertEqual(sent[1][1]['input'][1:3],output)
        self.assertEqual(sent[1][1]['input'][3],{'type':'function_call_output','call_id':'call_1','output':'ok'})

    def test_text_and_missing_model(self):
        adapter=ModelAdapter(lambda *args:{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'OK'}]}]})
        self.assertEqual(adapter.invoke(CFG,'key',[{'role':'user','content':'Hi'}]).content,'OK')
        _,model=adapter.tool_turn(CFG,'key',[],TOOLS,report_observed=True)
        self.assertEqual(model,'')

    def test_incomplete_and_malformed_fail(self):
        for result in ({'status':'incomplete','output':[]},{'status':'failed','output':[]},{'status':'completed','output':None}):
            with self.subTest(result=result), self.assertRaises(ProviderError):
                ModelAdapter(lambda *args:result).tool_turn(CFG,'key',[],TOOLS)

    def test_default_chat_unchanged(self):
        sent=[]
        def transport(url,body,headers):
            sent.append(url)
            return {'choices':[{'message':{'role':'assistant','content':'OK'}}]}
        cfg={k:v for k,v in CFG.items() if k!='api'}
        ModelAdapter(transport).tool_turn(cfg,'key',[],TOOLS)
        self.assertTrue(sent[0].endswith('/chat/completions'))


class DecisionRouteApiTest(unittest.TestCase):
    def test_activated_api_survives_resolution(self):
        from types import SimpleNamespace
        from personal_agent.decision_routes import DecisionRoutes
        routes=DecisionRoutes.__new__(DecisionRoutes)
        base={k:v for k,v in CFG.items() if k!='api'}
        routes.service=SimpleNamespace(decision_route=lambda:(base,'test-key'))
        config,key=routes._direct_resolve({'provider':'openai','requested_model':'gpt-6.1-terra','api':'responses'})
        self.assertEqual(config['api'],'responses')
        self.assertEqual(key,'test-key')
        self.assertNotIn('api',base)
        other,_=routes._direct_resolve({'provider':'compatible','requested_model':'other','api':'responses'})
        self.assertNotIn('api',other)

if __name__=='__main__': unittest.main()
