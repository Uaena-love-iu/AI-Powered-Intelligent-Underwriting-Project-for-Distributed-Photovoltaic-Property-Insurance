"""Start the delivered app without paths or secrets from the developer's PC."""
from pathlib import Path
import argparse,os,sys
ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0,str(ROOT/'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    import uvicorn
    print(f'Open http://127.0.0.1:{args.port}/evaluation')
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port)
