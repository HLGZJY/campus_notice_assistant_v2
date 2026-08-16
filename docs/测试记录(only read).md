# 开发人员测试填写记录文件

"""仅能由开发人员进行编辑"""

## 2026-8-16 抓取测试-crawl

### 目测能够触发抓取的位置：

#### 1

- scheduler job ，此时程序配置与scheduler读取到的配置相同，正确
  [INFO] apscheduler.scheduler Added job "定时抓取" to job store "default"
  scheduler 抓取间隔: 60 分钟 | 提取紧随抓取 | 每日 03:00 体检+提醒扫描已启用 | 配置每 60 秒监听2026-08-16 13:48:03,[INFO] apscheduler.executors.default Running job "定时抓取 (trigger: interval[1:00:00], next run at: 2026-08-16 14:48:03 CST)" (scheduled at 2026-08-16 13:48:03.130041+08:00)
  2026-08-16 13:48:03, [INFO] scheduler 调度器已并入后端进程（stop 由 API lifespan 统一处理）INFO: Application startup complete.
  2026-08-16 13:48:03, [INFO] scheduler 抓取汇总: 来源=2 发现=0 新增=0 跳过=0 变更=0 失败=02026-08-16 13:48:03,333 [INFO] scheduler job 'crawl' 结束: status=success 耗时=185ms

#### 2

通知预览页面“抓取”“深度抓取”：

- 默认什么也不填，就会采用系统配置-数据源配置 正确
- 时间配置配置处，启用三条 竞赛通知30天-1页、结果公示不限-1页、研究生院通知公告30天1页；通知预览页面抓取，选中竞赛通知和通知公告：
  - 测试结果：没有在抓取页面配置的结果公示也被进行抓取了，时间不限 正确
  - 时间范围 限定 正确
- 仅正文抓取 正确
  测试结果：数据源配置具有最高优先级，抓取优先级低于数据源配置

## 2026-8-26 提取测试 ，之前已经清除数据库

7-26 两条 22天内
7-29 一条 19天内
7-31 一条 17天内  
设置提取配置 单批上限10 最短正文长度 100 最大通知天数 17 必须含时间线索开启
提取结果：全部被提取，两条全提取，两条部分提取。耗时接近1分钟
猜测配置项没有生效。

## 提取测试-调用llm性能测试

4条通知
https://www.scuec.edu.cn/yjsy/info/1015/3841.htm
https://www.scuec.edu.cn/yjsy/info/1015/3843.htm
https://www.scuec.edu.cn/yjsy/info/1015/3845.htm
https://www.scuec.edu.cn/yjsy/info/1015/3847.htm

共分成 10个chunk 2390-2400左右
