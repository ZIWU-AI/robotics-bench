import mujoco
import numpy as np
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
if not (ROOT / 'authoring').exists():
    ROOT = ROOT.parent.parent / 'authoring'
LAYOUTS = {'development': (0.0, -0.58), 'near': (0.30, -0.50),
           'opponent': (0.0, 0.85), 'opponent-net': (0.25, 0.60)}

class Sim:
    def __init__(self, scene):
        params = json.loads((ROOT / 'scene.json').read_text())
        params.update(scene)
        self.scene = params
        self.layout = scene.get('layout', 'development')
        self.model = mujoco.MjModel.from_xml_path(str(ROOT / f'{self.layout}.xml'))
        self.data = mujoco.MjData(self.model)
        self.attempt = 1
        self.paddle_events = 0
        self.first_landing = None
        self.reset()

    def reset(self):
        mujoco.mj_resetDataKeyframe(self.model, self.data, self.model.key('initial').id)
        ball = int(self.model.joint('ball_free').qposadr[0])
        paddle = int(self.model.joint('paddle_free').qposadr[0])
        self.data.qpos[ball:ball + 3] = self.scene['ball']['position']
        self.data.qpos[paddle:paddle + 3] = self.scene['paddle']['position']
        mujoco.mj_forward(self.model, self.data)
        self.paddle_events = 0
        self.first_landing = None

    def snapshot(self):
        return {'attempt': self.attempt, 'time_s': float(self.data.time),
                'actuator_ctrl': self.data.ctrl.tolist(),
                'joint_qpos': self.data.qpos.tolist(),
                'ball': self.data.body('ball').xpos.tolist(),
                'ball_vel': self.data.body('ball').cvel[3:].tolist(),
                'paddle': self.data.body('paddle').xpos.tolist(),
                'paddle_vel': self.data.body('paddle').cvel[3:].tolist(),
                'target': list(LAYOUTS[self.layout]), 'contacts': self.contacts(),
                'paddle_events': self.paddle_events, 'first_landing': self.first_landing}

    def contacts(self):
        result = []
        for name in ('ball', 'paddle'):
            body = self.model.body(name).id
            for i, contact in enumerate(self.data.contact):
                g1, g2 = int(contact.geom1), int(contact.geom2)
                if self.model.geom_bodyid[g1] != body and self.model.geom_bodyid[g2] != body:
                    continue
                other = g2 if self.model.geom_bodyid[g1] == body else g1
                force = np.zeros(6)
                mujoco.mj_contactForce(self.model, self.data, i, force)
                result.append({'object': name, 'geom': self.model.geom(other).name,
                               'distance_m': float(contact.dist), 'normal_force_N': float(force[0]),
                               'tangent_force_N': float(np.linalg.norm(force[1:3])),
                               'position_m': contact.pos.tolist()})
        return result

    def trajectory(self, waypoints):
        previous = self.data.time
        for point in waypoints:
            target = np.asarray(point['ctrl'], dtype=float)
            count = round((float(point['t']) - previous) / self.model.opt.timestep)
            start = self.data.ctrl.copy()
            for i in range(count):
                u = (i + 1) / count
                self.data.ctrl[:] = start + (target - start) * (u * u * (3 - 2 * u))
                mujoco.mj_step(self.model, self.data)
                self._record_events()
            previous = float(point['t'])
        return self.snapshot()

    def retry(self):
        self.attempt += 1
        self.reset()

    def _record_events(self):
        ball = self.model.body('ball').id
        paddle = self.model.body('paddle').id
        ball_table = False
        paddle_ball = False
        for contact in self.data.contact:
            g1, g2 = int(contact.geom1), int(contact.geom2)
            b1, b2 = self.model.geom_bodyid[g1], self.model.geom_bodyid[g2]
            names = {self.model.geom(g1).name, self.model.geom(g2).name}
            if {b1, b2} == {ball, paddle}:
                paddle_ball = True
            if ball in (b1, b2) and 'tabletop' in names:
                ball_table = True
        if paddle_ball and self.paddle_events == 0:
            self.paddle_events = 1
        if ball_table and self.paddle_events == 1 and self.first_landing is None:
            pos = self.data.body('ball').xpos
            self.first_landing = [float(pos[0]), float(pos[1])]

    def success(self):
        if self.paddle_events != 1 or self.first_landing is None:
            return False
        tx, ty = LAYOUTS[self.layout]
        x, y = self.first_landing
        return abs(x - tx) < .075 and abs(y - ty) < .08
