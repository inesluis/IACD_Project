import random
import sys
import operator
import pandas as pd
import csv


class machine: 

    def __init__(self, power, duration):
        self.power = power
        self.duration = duration
        self.cooldown = None
        self.times = None
        
        
    def __str__(self):
        return f"{self.power}kwH, {self.duration}h"

class solution:

    def __init__(self, problem, positions):
        self.problem = problem
        self.positions = positions


    def eval_solution(self):
        paid_consume = 0
        for machine_idx, timestamps in self.positions.items():
            mac = self.problem.M[machine_idx]

            for timestamp in timestamps:
                for t in range(timestamp, timestamp + int(mac.duration)):
                    production_at_t = self.problem.P[t]
                    paid_consume += max(0, mac.power - production_at_t)

        return paid_consume

class problem:

    def __init__(self, productions, machines):
        self.M = machines
        self.P = productions
        self.n_m = len(machines)

    def __str__(self):
        res = "Machines: \n"
        for i,m in enumerate(self.M):
            res += f"M{i}:> " + str(m) + "\n"
        
        res += "PANEL PROD :" + str(self.P)
        return res
            
    @staticmethod
    def is_valid(machines, horizon):
        total_duration = sum(int(m.duration) for m in machines)
        return total_duration <= horizon
    
    @classmethod
    def from_textio(cls, f):
        machines = list()
        productions = list(map(int, f.readline().split()))
        machine_line = list(map(float, f.readline().split()))

        while machine_line:
            power, duration = machine_line
            machines.append(machine(power, duration))
            machine_line = list(map(float, f.readline().split()))

        if not cls.is_valid(machines, len(productions)):
            raise RuntimeError("This problem is not solvable")

        return cls(productions, machines)

    @classmethod
    def from_csv(cls,f_prod, f_machines):

        machines = None
        productions = None

        return cls(productions, machines)

    def empty_solution(self):
        return solution(self, {x: [] for x in range(self.n_m)}) #machines without t0


    def all_solutions_no_collisions(self,
                                    current_machine,
                                    schedule,
                                    occupied,
                                    current_cost,
                                    best_cost,
                                    best_schedule):

        if current_cost >= best_cost:
            return best_cost, best_schedule

        if current_machine == self.n_m:
            return current_cost, schedule.copy()

        mac = self.M[current_machine]
        duration = int(mac.duration)
        total_t = len(self.P)

        for t in range(total_t - duration + 1):

            if any(occupied[k] for k in range(t, t+duration)):
                continue

            added_cost = sum(
                max(0, mac.power - self.P[k]) for k in range(t, t+duration)
            )

            schedule.append((mac, t))

            for k in range(t, t+duration):
                occupied[k] = True


            best_cost, best_schedule = self. all_solutions_no_collisions(
                current_machine+1,
                schedule,
                occupied,
                current_cost + added_cost,
                best_cost,
                best_schedule
            )

            schedule.pop()

            for k in range(t, t+duration):
                occupied[k] = False

            return best_cost, best_schedule
            


    

def all_solutions_no_colisions(current_machine, current_time): #Generate All Solutions where only one machine per slot is allowed

    best = 10000
    total_t = len(production)
    schedule = []

    
    if current_machine == len(machines):
        cost = calc_solution(production, schedule)
        
        if cost < best:
            best = current_best
        return
    
    for t in range(current_time,total_t):
        
        #place this machine here
        all_solutions(current_machine, current_time+1)
        
        #do not place the machine in this time stamp and follow to the next
        schedule.append((current_machine, current_time))
        all_solutions(current_machine + 1, current_time+1)
        schedule.pop()
        


def main():
    pass

if __name__ == "__main__":

    p = problem.from_textio(sys.stdin)
    print(p)
