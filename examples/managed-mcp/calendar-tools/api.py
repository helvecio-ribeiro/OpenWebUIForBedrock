import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from repository import CalendarRepository

app = FastAPI(title='Shared Calendar API')
repository = CalendarRepository()

class EventInput(BaseModel):
    title: str
    start: str
    end: str | None = None
    description: str = ''
    all_day: bool = False
    location: str = ''
    reminder_minutes: int = 10

@app.get('/healthz')
def health(): return {'status': 'ok'}

@app.get('/events')
def search_events(query: str | None = None, start: str | None = None, end: str | None = None, count: int = 20):
    return {'events': repository.search(query, start, end, count)}

@app.post('/events')
def create_event(event: EventInput): return repository.create(**event.model_dump())

@app.patch('/events/{event_id}')
def update_event(event_id: str, changes: dict):
    try: return repository.update(event_id, **changes)
    except KeyError as exc: raise HTTPException(404, str(exc)) from exc

@app.delete('/events/{event_id}')
def delete_event(event_id: str):
    try: return repository.delete(event_id)
    except KeyError as exc: raise HTTPException(404, str(exc)) from exc

def run():
    import uvicorn
    uvicorn.run(app, host=os.getenv('CALENDAR_API_HOST', '127.0.0.1'), port=int(os.getenv('CALENDAR_API_PORT', '8091')))


if __name__ == '__main__':
    run()
