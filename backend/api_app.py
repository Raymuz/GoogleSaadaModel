"""GoogleSaada API: verified inference, real fixtures, GPT chat. No fabricated probabilities."""
import os, sys, logging
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
app=Flask(__name__, static_folder=None)
logging.basicConfig(level=logging.INFO)

@app.get('/')
def index(): return send_from_directory(BASE/'frontend','index.html')

@app.get('/api/health')
def health():
    required=['deployment_files/models','global_v3/v2g_staging/models']
    found=sum(len(list((BASE/p).glob('*.joblib'))) for p in required)
    return jsonify(status='ok' if found==13 else 'degraded',models_present=found)

def bridge():
    from backend.gs_v8_live_bridge import fixtures_summary, forecast, PredictionAbstained
    return fixtures_summary,forecast,PredictionAbstained

def fixture_list():
    getter,_,_=bridge(); d=getter()
    rows=d.get('fixtures',[])
    if isinstance(rows,dict): rows=list(rows.values())
    return d, rows

@app.get('/api/fixtures')
def fixtures():
    try:
        d,rows=fixture_list(); q=request.args.get('query','').lower().strip()
        if q:
            ignore={'predict','show','find','me','vs','against','matches','match','tomorrow','today','the','for','a','of','in','next','please','analyze','fixtures'}
            tokens=[w for w in __import__('re').findall(r'[a-z0-9]+',q) if w not in ignore and len(w)>1 and not w.isdigit()]
            if tokens:
                rows=[r for r in rows if any(t in (' '.join(str(r.get(k,'')) for k in ['home_name','away_name','competition'])).lower() for t in tokens)]
        return jsonify(fixtures=rows[:250],retrieved_utc=d.get('retrieved_utc'))
    except Exception as e:
        app.logger.exception('Fixture lookup failed')
        return jsonify(error='Verified fixture service unavailable'),503

@app.post('/api/predictions')
def predictions():
    payload=request.get_json(silent=True) or {}
    keys=payload.get('fixture_keys',[])
    if not isinstance(keys,list) or not 1<=len(keys)<=16 or len(set(map(str,keys)))!=len(keys) or any(not isinstance(x,str) or len(x)>160 for x in keys):
        return jsonify(error='Select 1–16 distinct valid fixtures'),422
    try:
        _,forecast,Abstained=bridge(); _,rows=fixture_list(); lookup={f['fixture_key']:f for f in rows}; results=[]
        for key in keys:
            f=lookup.get(key)
            if not f:
                results.append({'status':'ABSTAIN','fixture_key':key,'reason':'Fixture not in verified upcoming feed'});continue
            try:
                r=forecast(key)
                outputs=r.get('model_outputs',{})
                if not isinstance(outputs,dict) or not {'match_result','btts','over_2_5'}.issubset(outputs):
                    raise RuntimeError('Required model result unavailable')
                results.append({'status':'RESEARCH_PREDICTION','fixture_key':key,'fixture':f,'model_result':r})
            except Abstained as e:
                results.append({'status':'ABSTAIN','fixture_key':key,'fixture':f,'reason':str(e)})
            except Exception:
                app.logger.exception('Prediction failed')
                results.append({'status':'UNAVAILABLE','fixture_key':key,'fixture':f,'reason':'Model inference temporarily unavailable'})
        return jsonify(results=results,disclaimer='Research predictions only. No guarantee.' )
    except Exception:
        app.logger.exception('Prediction service unavailable')
        return jsonify(error='Prediction backend not ready'),503

@app.get('/api/recommendations')
def recommendations():
    try:
        _,rows=fixture_list()
        if not rows:return jsonify(results=[])
        _,forecast,Abstained=bridge(); out=[]
        for f in rows[:10]:
            try:
                value=forecast(f['fixture_key'])
                if {'match_result','btts','over_2_5'}<=set(value.get('model_outputs',{})):
                    out.append({'fixture':f,'status':'RESEARCH_PREDICTION','model_result':value})
            except Abstained: continue
            except Exception: app.logger.exception('Recommendation inference exception')
        return jsonify(results=out[:10])
    except Exception:
        app.logger.exception('Recommendations unavailable')
        return jsonify(error='Verified recommendations temporarily unavailable'),503

@app.get('/api/scores')
def scores():
    # Never pretend a scheduled fixture is a live score.
    return jsonify(results=[],message='Live score source is not connected in this clean deployment. No simulated scores are shown.')

SYSTEM='You are GoogleSaada, a football research assistant. Use available tools for real fixtures and trained-model predictions. Never invent fixtures, model probabilities, lineups, injuries, standings, odds, or results. State abstentions accurately. Be conversational and discuss user opinions with reasons; not betting guarantees.'
@app.post('/api/agent/chat')
def agent():
    payload=request.get_json(silent=True) or {}; messages=payload.get('messages',[])
    if not isinstance(messages,list) or not 1<=len(messages)<=12 or any(not isinstance(x,dict) or x.get('role') not in ['user','assistant'] or not isinstance(x.get('content'),str) or len(x['content'])>3500 for x in messages):
        return jsonify(error='Invalid chat history'),422
    if not os.getenv('OPENAI_API_KEY'): return jsonify(error='OpenAI API is not configured on this Space'),503
    try:
        from openai import OpenAI
        import json
        client=OpenAI(api_key=os.environ['OPENAI_API_KEY'],timeout=30,max_retries=1)
        tools=[{'type':'function','function':{'name':'list_verified_fixtures','description':'List verified upcoming fixtures.','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}}},{'type':'function','function':{'name':'predict_fixture','description':'Run saved football models for a verified fixture key. It may abstain.','parameters':{'type':'object','properties':{'fixture_key':{'type':'string'}},'required':['fixture_key'],'additionalProperties':False}}}]
        conversation=[{'role':'system','content':SYSTEM}]+messages
        for _ in range(3):
            response=client.chat.completions.create(model=os.getenv('GS_OPENAI_MODEL','gpt-4.1-mini'),messages=conversation,tools=tools,tool_choice='auto')
            reply=response.choices[0].message
            if not reply.tool_calls: return jsonify(reply=reply.content or 'No response available.')
            conversation.append(reply.model_dump(exclude_none=True))
            for tool in reply.tool_calls:
                args=json.loads(tool.function.arguments)
                if tool.function.name=='list_verified_fixtures':
                    _,rows=fixture_list(); q=args.get('query','').lower(); found=[f for f in rows if not q or q in (f.get('home_name','')+' '+f.get('away_name','')).lower()][:15]
                    value={'fixtures':found}
                elif tool.function.name=='predict_fixture':
                    key=args.get('fixture_key',''); _,rows=fixture_list()
                    if key not in {f['fixture_key'] for f in rows}:value={'status':'ABSTAIN','reason':'Unverified fixture key'}
                    else:
                        _,forecast,Abstained=bridge()
                        try:value=forecast(key)
                        except Abstained as ex:value={'status':'ABSTAIN','reason':str(ex)}
                else:value={'error':'Unsupported tool'}
                conversation.append({'role':'tool','tool_call_id':tool.id,'content':json.dumps(value,default=str)[:18000]})
        return jsonify(reply='The Agent reached its tool-call limit. Please ask about one match at a time.')
    except Exception:
        app.logger.exception('Agent provider failed')
        return jsonify(error='Agent service is temporarily unavailable. Please retry.'),503

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',7860)))
