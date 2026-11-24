"""Actuator-only scene authoring experiment; objects move through mj_step."""
import json
import argparse
import imageio.v2 as imageio
import mujoco
import numpy as np
from scipy.optimize import least_squares
from scene_preview import HERE, build, initial_state, arm_pose, camera

FINGERS = ('thumb', 'index', 'middle', 'ring', 'pinky')

def hand_target(model, scratch, side, center, targets):
    wrist = model.site(side + '_wrist_site').id
    rotation = scratch.site_xmat[wrist].reshape(3, 3).copy()
    origin = scratch.site_xpos[wrist].copy()
    errors = {}
    for finger, offset in targets.items():
        prefix = f'{side}_{side}_{finger}_'
        joints = [j for j in range(model.njnt) if model.joint(j).name.startswith(prefix)]
        addresses = model.jnt_qposadr[joints]
        bounds = model.jnt_range[joints]
        geom = model.geom(prefix + 'fingertip_collision').id
        goal = origin + rotation @ (center + offset)
        def residual(q):
            scratch.qpos[addresses] = q
            mujoco.mj_kinematics(model, scratch)
            return np.r_[scratch.geom_xpos[geom] - goal, .0002 * q]
        result = least_squares(residual, np.clip(np.full(len(joints), .4), bounds[:,0]+1e-6, bounds[:,1]-1e-6),
                               bounds=(bounds[:,0]+1e-6, bounds[:,1]-1e-6), max_nfev=150)
        residual(result.x)
        errors[finger] = float(np.linalg.norm(scratch.geom_xpos[geom] - goal))
    return errors

def controls(model, data):
    return data.qpos[model.jnt_qposadr[model.actuator_trnid[:,0]]].copy()

def plan(model, data):
    scratch = mujoco.MjData(model)
    scratch.qpos[:] = data.qpos
    downward = np.array([[0,1,0],[1,0,0],[0,0,-1]])
    specifications = {
        'left': (downward, np.array([.055,-.025,.14]),
                 {'thumb': [0,-.027,0], 'index': [0,0,.027], 'middle': [0,.027,0]}),
        'right': (downward, np.array([.055,0,.135]),
                  {'thumb': [0,.025,-.022], 'index': [0,.030,.022], 'middle': [0,.010,.022],
                   'ring': [0,-.010,.022], 'pinky': [0,-.030,.022]})}
    positions = {}
    report = {}
    for side, (rotation, center, offsets) in specifications.items():
        obj = data.geom('ball_geom' if side == 'left' else 'handle').xpos.copy()
        if side == 'left':
            positions[side] = obj + np.array([0.0, 0.015, 0.13])
        else:
            positions[side] = obj + np.array([0.0, 0.025, 0.14])
        error = arm_pose(model, scratch, side, positions[side], rotation)
        report[side] = {'wrist_error_m': error, 'finger_error_m': hand_target(model, scratch, side, center, offsets)}
    closed = controls(model, scratch)
    for side in specifications:
        for j in range(model.njnt):
            if model.joint(j).name.startswith(side+'_'):
                scratch.qpos[model.jnt_qposadr[j]] = 0
    opened = controls(model, scratch)
    for side, (rotation, _, _) in specifications.items():
        arm_pose(model, scratch, side, positions[side] + [0,0,.16], rotation)
    hover = controls(model, scratch)
    lifted = hover.copy()
    hand = [i for i,j in enumerate(model.actuator_trnid[:,0]) if not model.joint(j).name.startswith('arm_')]
    lifted[hand] = closed[hand]
    return hover, opened, closed, lifted, report

def object_contacts(model, data, name, side):
    body = model.body(name).id
    rows = []
    for i, c in enumerate(data.contact):
        g1,g2 = int(c.geom1),int(c.geom2)
        if g1 < 0 or g2 < 0:
            continue
        if model.geom_bodyid[g1] == body:
            obj,other = g1,g2
        elif model.geom_bodyid[g2] == body:
            obj,other = g2,g1
        else:
            continue
        force = np.zeros(6)
        mujoco.mj_contactForce(model, data, i, force)
        other_body = model.body(model.geom_bodyid[other]).name
        rows.append({'object_geom': model.geom(obj).name, 'other_geom': model.geom(other).name,
                     'other_body': other_body, 'hand': other_body.startswith(side+'_'),
                     'position_m': c.pos.tolist(), 'distance_m': float(c.dist),
                     'normal_force_N': float(force[0]), 'tangent_force_N': float(np.linalg.norm(force[1:3]))})
    return rows

def simulate_grasp(model, data, out, layout, video=True):
    hover, opened, closed, lifted, fit = plan(model, data)
    # The initial robot configuration is set once, before the experiment clock starts.
    data.qpos[model.jnt_qposadr[model.actuator_trnid[:,0]]] = hover
    data.ctrl[:] = hover
    mujoco.mj_forward(model, data)
    stages = [('settle', .6, hover), ('approach', 2., opened), ('close', 2., closed),
              ('lift', 2., lifted), ('hold', 1., lifted), ('release', 1., hover)]
    rows = []
    renderer = mujoco.Renderer(model, height=720, width=960) if video else None
    writer = imageio.get_writer(out / (layout+'-physical-grasp.mp4'), fps=30) if video else None
    cam = camera(True)
    cam.distance = 1.05
    cam.lookat[:] = [0,-1.08,.89]
    cam.azimuth, cam.elevation = 100,-35
    opt = mujoco.MjvOption()
    opt.geomgroup[3] = 0
    sample_interval = round(1 / (30 * model.opt.timestep))
    step = 0
    for stage, duration, target in stages:
        start = data.ctrl.copy()
        count = round(duration/model.opt.timestep)
        for i in range(count):
            u = (i+1)/count
            data.ctrl[:] = start + (target-start)*(u*u*(3-2*u))
            mujoco.mj_step(model,data)
            if step % sample_interval == 0:
                row = {'t': float(data.time), 'stage': stage, 'objects': {}}
                for name,side in [('ball','left'),('paddle','right')]:
                    wrist = model.site(side+'_wrist_site').id
                    pos = data.body(name).xpos.copy()
                    row['objects'][name] = {'position_m': pos.tolist(),
                        'relative_to_hand_m': ((pos-data.site_xpos[wrist]) @ data.site_xmat[wrist].reshape(3,3)).tolist(),
                        'contacts': object_contacts(model,data,name,side)}
                rows.append(row)
                if video:
                    renderer.update_scene(data,camera=cam,scene_option=opt)
                    writer.append_data(renderer.render())
            step += 1
        print(stage, {n: data.body(n).xpos.round(4).tolist() for n in ['ball','paddle']},
              {side: data.site(side+'_wrist_site').xpos.round(4).tolist() for side in ('left','right')}, flush=True)
        if video:
            renderer.update_scene(data,camera=cam,scene_option=opt)
            imageio.imwrite(out/(layout+'-physical-'+stage+'.png'), renderer.render())
    if video:
        writer.close()
        renderer.close()
    result = {'planning': fit, 'samples': rows, 'warnings': data.warning.number.tolist()}
    (out/(layout+'-physical-grasp.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return {'planning':fit, 'warnings':result['warnings']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-video', action='store_true')
    args = parser.parse_args()
    model = build('development')
    data,_ = initial_state(model)
    print(simulate_grasp(model,data,HERE/'evidence','development', not args.no_video))
