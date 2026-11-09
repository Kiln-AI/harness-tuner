# Kiln Harness Tuner

An agentic optimize loop for Kiln tasks: it plans experiments against a task's
baseline run config, builds and measures each one on the train split, and
promotes a candidate only when the val split agrees. Every agent in it is an
agent-CLI session (`claude -p` by default), every measurement is a Kiln eval
job, and test is never read.
