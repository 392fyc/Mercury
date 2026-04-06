# Adapters

外部项目适配层。每个 adapter 只做接口转换，不包含业务逻辑。

## 规范

```
adapters/
  {project-name}/
    README.md           # 挂载说明: 挂载了什么、为什么、适配了什么
    adapter.ts 或 .py   # 接口转换代码
    UPSTREAM.md         # 上游版本记录、已知不兼容项
```

## 约束

- 适配层不超过 200 行。超过说明耦合过深，需重新评估挂载方式。
- 外部项目通过 git submodule 挂载到 `modules/` 目录。
- 详见 `.mercury/docs/DIRECTION.md` 第四章。
