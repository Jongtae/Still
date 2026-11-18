"""Model-free OpenAI Responses fixtures shared by evaluation and test transports."""


def responses_reply(message, model=None):
    """Encode an existing scripted Chat message as a raw REST Responses fixture."""
    output = []
    if message.get('content') is not None:
        output.append({'type': 'message', 'role': 'assistant',
                       'content': [{'type': 'output_text', 'text': message['content']}]})
    for call in message.get('tool_calls') or []:
        output.append({'type': 'function_call', 'id': 'fc_' + call['id'], 'call_id': call['id'],
                       **call['function'], 'status': 'completed'})
    data = {'id': 'resp_fixture', 'status': 'completed', 'output': output}
    if model is not None:
        data['model'] = model
    return data
