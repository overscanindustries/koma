"""Boot the grdui Playground (emulator), dump raw 4bpp OSD pages + raw palette for: ready, menu, down."""
import json, sys, time, shutil
from grdui.playground import Playground
from grdui.session import Session, OSD_STRIDE, OSD_W, OSD_H
out = sys.argv[1]  # e.g. tools/komaset/bridge/testdata (then rename <tag>.osd to osd-<tag>.bin)
cap = []
def factory(*a, **k):
    s = Session(*a, **k); cap.append(s); return s
pg = Playground(session_factory=factory).start()
def wait_idle(t=300):
    t0 = time.time()
    while time.time() - t0 < t:
        s = pg.snapshot()
        if s['error']: raise SystemExit(s['error'])
        if s['ready'] and not s['busy']: return s
        time.sleep(0.5)
    raise SystemExit('timeout')
def dump(tag):
    s = cap[-1]
    st = s.state()
    page = bytes(s.m.uc.mem_read(s.page(), OSD_STRIDE * OSD_H))
    open(f'{out}/{tag}.osd', 'wb').write(page)
    json.dump({'palette': s.ven.palette(), 'state': st, 'page': s.page(), 'stride': OSD_STRIDE, 'w': OSD_W, 'h': OSD_H},
              open(f'{out}/{tag}.json', 'w'))
    print(tag, 'dumped', st)
wait_idle(); dump('ready')
pg.action('menu'); time.sleep(1); wait_idle(); dump('menu')
pg.action('down'); time.sleep(1); wait_idle(); dump('down')
s = cap[-1]
pg.close()
shutil.rmtree(s.out, ignore_errors=True)
print('done')
