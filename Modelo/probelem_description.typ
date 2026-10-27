#set document(
              title : [Construction of schedules to optimize energy consumption costs],
            )
#set page(paper: "a4", 
          margin: (top: 2.5cm, bottom:2.5cm, x:2.5cm),
          header: [
          _Problem Definition_ 
          #h(1fr) 
          Departamento de Engenheria Informática, UC],
          footer: context[
          _Luís B._
          #h(1fr)
          #counter(page).display("1 of 1")
          ],
          columns: 2,
          )
#set par(justify:true)
#set heading(level: 1,
             depth: 1,
             offset: 3,
             numbering: "1",
             supplement: auto,
             outlined: true,
             bookmarked: true, 
             hanging-indent: 5cm,
             )
#set text(font: "EB Garamond")
#let datas_novas = "21 October, 2026"


#title()
= Introduction

To reduce energy costs, many households and business owners install renewable energy systems, such as solar panels. These technologies are dependent on outside data, such as meteorological data that is out of our control. However, we can control some things, such as scheduling the machines.  These changes consist of adjustments to the running of the machines, so that the highest consumption times can be covered also by the highest solar energy production times.



= Task

The main objective is to determine the best time span to run a specific type of machine, reducing the amount of energy from the Company that is needed for that week and thus reducing the costs of the User.

= Detailed Description

The problem statement is the following for each one of the users. The User has a list of machines, each one has some definitions, such as its consumption rate and the time it takes to run. The user has also a baseline schedule, this schedule is for one week and in each day it has the times that each machine will be working on. Machines can not work simultaneously and their running time is multiples of one hour.

The schedule $S$ is a list with $24 times 7 $ elements to represent all the hours in a week. 

A machine instance is $m_i = (p_i, d_i, c_i,t_i)$ 

- power
- duration
- cool down
- times
- start times

Continuous (at least at hour level) -- graph that has the production expected for each hour for each day of the week. The Consume of one user can be $C_"total" = C_p plus C_c plus R,$ $C_p "and" C_c$ are respectively the consume from the panel and the consume from the company that was "consumed" by the machines the user has. Those are the _Tracked_ machines. If we are going for a realistic approach on this problem the $C_"total"$ would be the consume from the _tracked_ and the _untracked_ machines, these are represented by the variable $E$. 

The total production of a week is 

$C_"total" = sum_i^M (m_i,p_i times m_i,t_i))$

So, lets assume that for a given week $w$                                                                                                                                                                                                                                                                                                                                                                                                                                    the expected consume from the tracked machines is $E_"cp"$









= Sets and Parameters
[
Let $T = {0, 1, ..., 167}$ denote the set of hourly time slots in a
one-week planning horizon, and let $J$ denote the set of appliance
operations that must be scheduled.

Each operation $j in J$ is characterized by:
- $p_j$: power consumption, measured in kW.
- $d_j$: duration, measured in hours.
- $A_j$: set of valid starting times.

For each time slot $t in T$, let:
- $g_t$ denote the expected photovoltaic energy production, in kWh.
- $e_t$ denote the energy consumption of untracked appliances, in kWh.

The photovoltaic production profile is estimated using meteorological
data and a physical model of the photovoltaic installation.

= Decision Variables

Let $x_(j,t)$ be a binary decision variable indicating whether
operation $j$ starts at time slot $t$:

$ x_(j,t) in {0, 1} $

The variable $y_(j,t)$ indicates whether operation $j$ is running
during time slot $t$:

$ y_(j,t) = sum_(s in A_j, s <= t < s + d_j) x_(j,s) $





= Instance File
