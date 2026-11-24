"""MuJoCo physics boundary used by the authoring preview and Oracle."""
import mujoco
import numpy as np
from scene_preview import build, initial_state

class PhysicsScene:
    def __init__(self, layout='development'):
        self.layout = layout
        self.model = build(layout)
        self.data, self.ik_error = initial_state(self.model)
        self._actuator_joints = self.model.actuator_trnid[:, 0].copy()
        self.data.ctrl[:] = self.data.qpos[self.model.jnt_qposadr[self._actuator_joints]]
        mujoco.mj_forward(self.model, self.data)

    def reset(self):
        mujoco.mj_resetData(self.model, self.data)
        self.data, self.ik_error = initial_state(self.model)
        self.data.ctrl[:] = self.data.qpos[self.model.jnt_qposadr[self._actuator_joints]]
        mujoco.mj_forward(self.model, self.data)

    def step(self, actuator_target, duration):
        target = np.asarray(actuator_target, dtype=float)
        if target.shape != (self.model.nu,):
            raise ValueError('actuator target has wrong shape')
        count = round(float(duration) / self.model.opt.timestep)
        start = self.data.ctrl.copy()
        for i in range(count):
            u = (i + 1) / count
            self.data.ctrl[:] = start + (target - start) * (u * u * (3 - 2 * u))
            mujoco.mj_step(self.model, self.data)

    def contacts(self, body_name):
        body_id = self.model.body(body_name).id
        result = []
        for index, contact in enumerate(self.data.contact):
            g1, g2 = int(contact.geom1), int(contact.geom2)
            if self.model.geom_bodyid[g1] != body_id and self.model.geom_bodyid[g2] != body_id:
                continue
            other = g2 if self.model.geom_bodyid[g1] == body_id else g1
            force = np.zeros(6)
            mujoco.mj_contactForce(self.model, self.data, index, force)
            result.append({'geom': self.model.geom(other).name, 'distance_m': float(contact.dist),
                           'normal_force_N': float(force[0]),
                           'tangent_force_N': float(np.linalg.norm(force[1:3])),
                           'position_m': contact.pos.copy().tolist()})
        return result

    def state(self):
        return {'time_s': float(self.data.time),
                'ball_position_m': self.data.body('ball').xpos.copy().tolist(),
                'paddle_position_m': self.data.body('paddle').xpos.copy().tolist(),
                'ball_contacts': self.contacts('ball'),
                'paddle_contacts': self.contacts('paddle')}
