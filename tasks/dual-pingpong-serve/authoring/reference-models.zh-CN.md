# 视频模型与场景对应

参考项目：HeegerGao/ManiLadder-Benchmark，commit `936c50effd7ab2e312bcb8cf98d142f6d9a20ef4`。

任务定义 `maniladder/envs/rigid_body/dual_arm_dex/level_4/send_pingpong.py` 的 `SUPPORTED_ROBOTS` 指定 `panda_leaphand_left` 与 `panda_leaphand`。对应 agent 文件加载左右 Panda + LEAP URDF。

| 部件 | 原场景 | 本次替换 | 许可 |
|---|---|---|---|
| 机械臂 | 共用机身 FR3 Duo | 两台独立 Franka Panda，Menagerie panda_nohand.xml | Apache-2.0 |
| 灵巧手 | Sharpa Wave，各22关节 | 参考项目左右 LEAP Hand，各16关节、四指 | MIT，许可证保存在 licenses/ |

Panda 资产固定于 Menagerie commit `370c43965323c0944b433b202aac5058b5f907a9`，由 prepare_assets.py 复制并记录哈希。LEAP 手子树由 prepare_leap.py 从参考 URDF 转换，保留惯量、关节轴、限位、网格、碰撞形状与左右安装偏移；手指 PD 参数沿用参考 agent 的 stiffness=400、damping=10、force_limit=50。对应资产哈希记录在 leap-provenance.json。

## 坐标映射

参考场景台面高为0。本任务保留标准球台尺寸、台面高0.76及目标定义，将参考坐标映射为 `(x,y,z)=(-y_ref,x_ref-1.0,z_ref+0.76)`。

原左臂底座 (-0.15,-0.65,0)、偏航90度，对应 (0.65,-1.15,0.76)、偏航180度。原右臂底座 (-0.15,0.65,0)、偏航-90度，对应 (-0.65,-1.15,0.76)、偏航0度。左右名称沿用上游 agent 定义，两臂在台面上独立固定、相对摆放。

参考拍垫位于拍柄参考原点沿正 y_ref 方向0.2米处，映射后拍面支撑位于柄内侧，柄向正 x 悬伸。当前圆拍仍使用任务规定的160 mm直径，柄中心为 (0.275,-1.0,0.81515)。参考任务球垫高30 mm、拍垫高50 mm，本场景同步这两个高度。

初始图使用张手待机姿态。视频任务使用 Genesis；本任务继续使用 MuJoCo 3.3.7 和重力9.81 m/s²，球半径20 mm。参考源码中重力1.0 m/s²和球半径33 mm不纳入本任务参数。

## 本次检查

两臂共14个关节、双手共32个关节，总执行器46个。初始姿态接触数0；静置0.5秒后接触仅为球—球垫、拍面胶皮—拍垫。证据位于 evidence/panda-leap-static.json。Oracle 方案保留在 oracle-design.zh-CN.md，等待用户审阅。
