import asyncio
import aiohttp
import time
import signal
import copy


class StateMachine:
    def __init__(self, session, config, credentials, collector):
        self.session = session
        self.config = config
        self.credentials = credentials
        self.current_credential = None
        self.state = config.get("initial_state", "INITIAL")
        self.metrics = []
        self.collector = collector

    async def execute_task(self, task):
        start_time = time.monotonic()
        try:
            method = getattr(self.session, task["method"].lower())
            params = task.get("params", {})
            if self.current_credential and "data" in params:
                params["data"].update(self.current_credential)

            async with method(task["url"], **params) as response:
                response_time = time.monotonic() - start_time
                self.metrics.append({
                    "state": self.state,
                    "task": task["name"],
                    "status": response.status,
                    "response_time": response_time,
                    "error": None,
                })
                return response.status
        except aiohttp.ClientError as exc:
            response_time = time.monotonic() - start_time
            self.metrics.append({
                "state": self.state,
                "task": task["name"],
                "status": None,
                "response_time": response_time,
                "error": str(exc),
            })
            return None

    async def run(self):
        while self.state in self.config["states"]:
            if not self.collector.running:
                break

            state_config = self.config["states"][self.state]
            for task in state_config["tasks"]:
                if not self.collector.running:
                    break

                if self.state == "LOGIN" and self.credentials:
                    self.current_credential = self.credentials.pop(0)

                status = await self.execute_task(task)
                for transition in state_config["transitions"]:
                    if transition["condition"](status):
                        if transition.get("terminate", False):
                            self.state = transition["next_state"]
                            return
                        self.state = transition["next_state"]
                        break
                else:
                    continue
                break


class MetricsCollector:
    def __init__(self):
        self.running = True
        self.metrics = []

    def stop(self, *args):
        print("\nGracefully shutting down...")
        self.running = False

    def add_metrics(self, new_metrics):
        self.metrics.extend(new_metrics)

    def display_metrics(self):
        print("\nCollected Metrics:")
        for metric in self.metrics:
            print(metric)


async def main():
    config = {
        "initial_state": "LOGIN",
        "states": {
            "LOGIN": {
                "tasks": [
                    {
                        "name": "login",
                        "method": "POST",
                        "url": "https://example.com/login",
                        "params": {"data": {}},
                    }
                ],
                "transitions": [
                    {"condition": lambda status: status == 200, "next_state": "FETCH_DATA"},
                    {"condition": lambda status: status != 200, "next_state": "LOGIN_FAILED", "terminate": True},
                ],
            },
            "FETCH_DATA": {
                "tasks": [
                    {
                        "name": "fetch_data",
                        "method": "GET",
                        "url": "https://example.com/data",
                    }
                ],
                "transitions": [
                    {"condition": lambda status: status == 200, "next_state": "COMPLETE"},
                    {"condition": lambda status: status != 200, "next_state": "ERROR", "terminate": True},
                ],
            },
            "LOGIN_FAILED": {"tasks": [], "transitions": []},
            "ERROR": {"tasks": [], "transitions": []},
            "COMPLETE": {"tasks": [], "transitions": []},
        },
    }

    credentials = [
        {"username": "user1", "password": "pass1"},
        {"username": "user2", "password": "pass2"},
        {"username": "user3", "password": "pass3"},
    ]

    collector = MetricsCollector()
    signal.signal(signal.SIGINT, collector.stop)

    async with aiohttp.ClientSession() as session:
        try:
            while collector.running:
                state_machines = [
                    StateMachine(session, config, copy.copy(credentials), collector)
                    for _ in range(len(credentials))
                ]
                tasks = [asyncio.create_task(state_machine.run()) for state_machine in state_machines]

                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                for state_machine in state_machines:
                    collector.add_metrics(state_machine.metrics)

        except asyncio.CancelledError:
            pass

    collector.display_metrics()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user.")
