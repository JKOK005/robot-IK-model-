import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--interactive", action="store_true")
# parser.add_argument("--collision", action="pqp")
args = parser.parse_args()

import openravepy as op
import trajoptpy
import json
import time
import IK
import IPython
from math import pi, ceil
import numpy as np

class Motion_planning:
	
	def __init__(self, env_file, arm_to_plan, indx=0):
		self.env = op.Environment()
		self.env.StopSimulation()
		self.env.Load(env_file)
		self.env.SetViewer('qtcoin')

		self.robot 		= self.env.GetRobots()[indx]
		self.left_arm 	= self.robot.GetManipulator('left_arm')
		self.right_arm 	= self.robot.GetManipulator('right_arm')

	# env.Load("env.xml")
	# env.Load("../data/table.xml")

	def init_collision_checker(self, checker, collision_options):
		"""
		params: checker <string>: The name of the Checkser used for collision detection
				collision_options <list><op::CollisionOptions>
		"""
		collisionChecker = op.RaveCreateCollisionChecker(self.env, checker)

		j = 0
		for i in collision_options:
			j = j|i

		collisionChecker.SetCollisionOptions(j)
		self.env.SetCollisionChecker(collisionChecker)

	# collisionChecker = op.RaveCreateCollisionChecker(env,'pqp')
	# collisionChecker.SetCollisionOptions(op.CollisionOptions.Distance|op.CollisionOptions.Contacts)

	def __set_request(self, manip, joint_target, n_steps=10):
		"""
		Starts with a straight line trajectory to the end goal
		"""
		self.plan_arm 	= manip 	# Sets the planning arm for visual purpose
		self.target 	= joint_target

		self.request = {
		  "basic_info" : {
			"n_steps" : n_steps,
			"manip" : manip, # see below for valid values
			"start_fixed" : True # i.e., DOF values at first timestep are fixed based on current robot state
		  },

		  "costs" : [
		  {
			"type" : "joint_vel", # joint-space velocity cost
			"params": {"coeffs" : [100,100,1]} # a list of length one is automatically expanded to a list of length n_dofs

		  },
		  {
			"type" : "collision",
			"params" : {
			  "coeffs" : [10000], # penalty coefficients. list of length one is automatically expanded to a list of length n_timesteps
			  "dist_pen" : [0.1], # robot-obstacle distance that penalty kicks in. expands to length n_timesteps

			  "continuous" : True
			}
		  },

		  {
			"type" : "collision",
			"params" : {
			  "coeffs" : [10000], # penalty coefficients. list of length one is automatically expanded to a list of length n_timesteps
			  "dist_pen" : [0.1], # robot-obstacle distance that penalty kicks in. expands to length n_timesteps

			  "continuous" : False
			}
		  }    
		  ],

		  "constraints" : [
		  {
			"type" : "joint", # joint-space target
			"params" : {"vals" : joint_target} # length of vals = # dofs of manip

			},
		  {
			"type"    : "cart_vel",
			"name"    : "s0_vel",
			"params"  : {
			  "max_displacement"  : 1,
			  "first_step"        : 0,
			  "last_step"         : n_steps -1, #inclusive
			  "link"              : "s0"
			}
		  }, 
		  {
			"type"    : "cart_vel",
			"name"    : "s1_vel",
			"params"  : {
			  "max_displacement"  : 1,
			  "first_step"        : 0,
			  "last_step"         : n_steps -1, #inclusive
			  "link"              : "s1"
			}
		  }
		  ]
		}

		return


	def get_robot(self):
		return self.robot

	def get_manip(self, name):
		if 	 name == "left_arm" : return self.left_arm
		elif name == "right_arm" : return self.right_arm

	def set_manip(self, name, DOF):
		assert name == "left_arm" or name == "right_arm"
		if name == "left_arm":
			self.left_arm_DOF = DOF
		elif name == "right_arm":
			self.right_arm_DOF = DOF
		pass

		self.get_robot().SetDOFValues(DOF, self.get_manip(name).GetArmIndices())

	def optimize(self, manip, joint_target, algorithm):
		"""
		Optimization of the robot via initialization of motion through different way points
		We start by performing optimization in a straight line towards the joint target
		We check if the optimal path choosen is safe by iterating through the final trajectory 
			If path is safe -> Return trajectory
			If path is not safe -> Initialize to a different trajectory

		The sequence of optimization is done as such
			1) Straight line
			2) Retraction of arm
			3) Stationary
		"""
		trajectory 		= self.__init_traj(manip=manip, joint_target=joint_target, algorithm=algorithm) # Performs initial RRT planning
		self.traj 		= []
		IK_obj 			= IK.dVRK_IK_simple()

		for i in range(len(trajectory) -1):
			step_dis_ratio = 0.5
			eff1 	= IK_obj.get_endEffector_fromDOF(trajectory[i +1])
			eff2 	= IK_obj.get_endEffector_fromDOF(trajectory[i])
			dis 	= np.linalg.norm(np.array(eff1) - np.array(eff2))
			n_steps = max(int(ceil(dis / step_dis_ratio)) , 3)
			self.__set_request(manip=manip, joint_target=trajectory[i +1], n_steps=n_steps)
			self.robot.SetDOFValues(trajectory[i], self.robot.GetManipulator(self.plan_arm).GetArmIndices())

			for j in range(3):
				try: del self.request['init_info']
				except KeyError: pass

				# if i == 0:
					# self.request.update({"init_info" : {"type" : "given_traj", "data" : traj}})	# Way point initialization after path planning

				if j == 0:
					self.request.update({"init_info" : {"type" : "straight_line", "endpoint" : self.target}})	# Straight line initialization

				elif j == 1:
					limit = self.robot.GetDOFLimits()[0][-1]		# Gets the maximum retraction distance for our model robot
					pull_back = eval('self.' + self.plan_arm + '_DOF')
					pull_back[-1] = max(limit, pull_back[-1] -5)
					self.request.update({"init_info" : {"type" : "straight_line", "endpoint" : pull_back}})	# Straight line initialization

				elif j == 2:
					self.request.update({"init_info" : {"type" : "stationary"}})	# Straight line initialization

				jd 			= json.dumps(self.request) 					# convert dictionary into json-formatted string
				prob 		= trajoptpy.ConstructProblem(jd, self.env) 	# create object that stores optimization problem
				result 		= trajoptpy.OptimizeProblem(prob) 			# do Optimization
				
				if self.__check_safe(result.GetTraj()):
					print(trajectory[i])
					print(trajectory[i +1])
					break
				elif j == 2:
					raise Exception('No path is safe')
			self.traj += result.GetTraj().tolist()
		return

	def simulate(self):
		for t in self.traj:
			self.robot.SetDOFValues(t, self.robot.GetManipulator(self.plan_arm).GetArmIndices())
			time.sleep(0.1)

	def __check_safe(self, trajectory):
		kin = self.env.GetKinBody('dvrk')
		self.env.GetCollisionChecker().SetCollisionOptions(op.CollisionOptions.Contacts)

		for t in trajectory:			
			self.robot.SetDOFValues(t, self.robot.GetManipulator(self.plan_arm).GetArmIndices())
			flag = self.env.CheckCollision(kin.GetLinks()[3], kin.GetLinks()[6])		# Checks for collisions between 2 cylinder arms of the robot

			if flag == True:
				return False		# That means that collision happened
			time.sleep(0.05)
		return True

	def __init_traj(self, manip, joint_target, algorithm):
		"""
		Creates an initial trajectory plan between start and end goal poses
		"""
		# Issue with the init and goal configuratons -> Planner thinks that the arms are in collision

		Algo 		= "OMPL_" + algorithm
		planner 	= op.RaveCreatePlanner(self.env, Algo)		# Initializes a planner with algorithm
		simplifier 	= op.RaveCreatePlanner(self.env, 'OMPL_Simplifier')
		# self.env.GetCollisionChecker().SetCollisionOptions(op.CollisionOptions.Contacts)

		with self.env:
			arm_indx = self.get_manip(name=manip).GetArmIndices()
			self.get_robot().SetActiveDOFs(arm_indx)		# Plan for only the arm specified in manip
			self.get_robot().SetActiveDOFValues(self.right_arm_DOF)
			self.get_robot().SetActiveManipulator(self.get_manip(name=manip))

		params 		 = planner.PlannerParameters()				# Creates an empty param to be filled 
		params.SetRobotActiveJoints(self.robot)
		params.SetGoalConfig(joint_target[0])

		with self.env:
			with self.get_robot():
				print "Starting intial plan using {:s} algorithm".format(algorithm)
				traj = op.RaveCreateTrajectory(self.env, '')
				planner.InitPlan(self.get_robot(), params)
				result = planner.PlanPath(traj)
				assert result == op.PlannerStatus.HasSolution
				
				print 'Calling the OMPL_Simplifier for shortcutting.'
				simplifier.InitPlan(self.get_robot(), op.Planner.PlannerParameters())
				result = simplifier.PlanPath(traj)
				assert result == op.PlannerStatus.HasSolution

				trajectory = [traj.GetWaypoint(i).tolist() for i in range(traj.GetNumWaypoints())]
				return trajectory

if __name__ == "__main__":	
	joint_start1 = [3.14/3, 3.14/4, 1]
	joint_start2 = [-3.14/5, 3.14/4, 0]
	manip 		 = "right_arm"

	planner = Motion_planning('env.xml', "right_arm")
	planner.init_collision_checker('pqp', [op.CollisionOptions.Contacts])

	planner.set_manip(name="left_arm", DOF=joint_start1)
	planner.set_manip(name="right_arm", DOF=joint_start2)

	IK_obj = IK.dVRK_IK_simple()                                # Creates an IK object 
	endEff = IK_obj.get_endEffector_fromDOF([-3.14/2, 3.14/4, 0])
	joint_target = IK_obj.get_joint_DOF(endEff)     

	planner.optimize(manip, joint_target, algorithm="RRTConnect")
	planner.simulate()

	IPython.embed()
