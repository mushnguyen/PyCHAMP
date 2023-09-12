r"""
The code is developed by Chung-Yi Lin at Virginia Tech, in April 2023.
Email: chungyi@vt.edu
Last modified on Sep 6, 2023
"""
import numpy as np
import mesa
from scipy.stats import truncnorm
from .opt_model import OptModel
from ..util import Box

class Farmer(mesa.Agent):
    """
    Represents a Farmer agent in an agent-based model.

    Attributes
    ----------
    agt_id : str or int
        Unique identifier for the agent.
    config : dict or DotMap
        General configuration information for the model.
    agt_attrs : dict
        Agent-specific attributes for simulation.
    prec_aw_dict : dict
        Dictionary with precipitation data with field_id as its key and annual 
        precipitation during growing season as its value.
    aquifers : dict
        A dictionary contain aquifer objects. The key is aquifer id.
    model : object
        Reference to the overarching MESA model instance.
    """
    def __init__(self, agt_id, mesa_model, config, agt_attrs, 
                 fields, wells, finance, aquifers,
                 ini_year,
                 crop_options, tech_options, **kwargs):
        """
        Initialize a Farmer agent.

        Parameters
        ----------
        agt_id : str or int
            Unique identifier for the agent.
        mesa_model : object
            Reference to the overarching MESA model instance.
        config : dict or DotMap
            General configuration information for the model.
        agt_attrs : dict
            Agent-specific attributes for simulation.
        fields : dict
            A dictionary contains the fields the farmer owns.
        wells : dict
            A dictionary contains the wells the farmer owns.
        finance : object
            Financial object for the agent.
        aquifers : dict
            A dictionary contain aquifer objects. The key is aquifer id.
        ini_year : int
            Initial simulation year
        crop_options : dict
            Available crop options for the farmer.
        tech_options : dict
            Available technology options for the farmer.
        kwargs : dict, optional
            Additional optional arguments.

        Notes
        -----
        The `kwargs` could contain any additional attributes that you want to
        add to the Farmer agent. Available keywords include
        fix_state : str
            "Imitation", "Social comparison", "Repetition", "Deliberation", 
            and "FixCrop".  
        rngen : object
            Numpy random generator
        
        """
        super().__init__(agt_id, mesa_model)
        # MESA required attributes
        self.unique_id = agt_id
        self.agt_type = "Farmer"

        # Load other kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
            
        self.fix_state = kwargs.get("fix_state")
        #========
        self.agt_id = agt_id
        self.crop_options = crop_options
        self.tech_options = tech_options

        # Load agt_attrs
        self.dm_args = agt_attrs["decision_making"]
        self.agt_ids_in_network = agt_attrs["agt_ids_in_network"]
        self.water_rights = agt_attrs["water_rights"]

        # Load config
        self.load_config(config)

        # Assign agt's assets
        self.aquifers = aquifers
        self.fields = fields
        self.Fields = Box(fields) # same as self.fields but with dotted access
        self.wells = wells
        self.Wells = Box(wells) # same as self.fields but with dotted access
        self.finance = finance

        # Initialize CONSUMAT
        self.state = None
        self.satisfaction = None
        self.expected_sa = None     # From optimization
        self.uncertainty = None
        self.irr_vol = None         # m-ha
        self.profit = None
        self.yield_pct = None
        
        self.scaled_profit = None
        self.scaled_yield_pct = None
        
        self.needs = {}
        self.agts_in_network = {}   # This will be dynamically updated in a simulation
        self.selected_agt_id_in_network = None # This will be populated after social comparison
        
        # Some other attributes
        self.t = 0
        self.current_year = ini_year
        self.percieved_risks = None
        self.perceived_prec_aw = None
        
        # Initialize dm_sol (mimicing opt_model's output)
        dm_sols = {}
        for fi, field in self.fields.items():
            dm_sols[fi] = {}
            dm_sols[fi]["i_crop"] = field.i_crop
            dm_sols[fi]["pre_i_crop"] = field.pre_i_crop
            dm_sols[fi]["i_te"] = field.te
            dm_sols[fi]["pre_i_te"] = field.pre_te
        # Run the optimization to solve irr depth with every other variables
        # fixed.
        self.dm_sols = self.make_dm(None, dm_sols=dm_sols, init=True)
        # Run the simulation to calculate satisfication and uncertainty
        self.run_simulation() # aquifers

    def load_config(self, config):
        """
        Load config.

        Parameters
        ----------
        config : dict
            General configuration of the model.

        Returns
        -------
        None.

        """
        config_consumat = config["consumat"]
        if self.dm_args["alphas"] is None:
            self.dm_args["alphas"] = config_consumat["alpha"]
        self.dm_args["scale"] = config_consumat["scale"]

        self.sa_thre = config_consumat["satisfaction_threshold"]
        self.un_thre = config_consumat["uncertainty_threshold"]
        self.n_s = config["field"]["area_split"]

        self.config_gurobi = config["gurobi"]
        self.config = config  # for opt only

    def process_percieved_risks(self, par_perceived_risk):
        for fi, field in self.fields.items():
            #Compute perceived_prec_aw based on perceived_risk
            truncated_normal_pars = field.truncated_normal_pars
            percieved_risks = {
                crop: 0 if crop=="fallow" else \
                    round(
                        truncnorm.ppf(
                            q=par_perceived_risk,
                            a=truncated_normal_pars[crop][0],
                            b=truncated_normal_pars[crop][1],
                            loc=truncated_normal_pars[crop][2],
                            scale=truncated_normal_pars[crop][3]),
                        4
                    ) for crop in self.crop_options}
        self.percieved_risks = percieved_risks
    
    def update_perceived_prec_aw(self, par_forecast_trust, year):
        # year != self.current_year (should be one step ahead)
        # Blend agt's original perceived prec_aw with the perfect prec_aw
        # forecast before the optimization.
        fotr = par_forecast_trust
        perceived_prec_aw = {}
        for fi, field in self.fields.items():
            percieved_risks = self.percieved_risks
            prec_aw = field.prec_aw_step[year]
            perceived_prec_aw_f = {
                crop: round(percieved_risks[crop]*(1-fotr) + prec_aw[crop]*fotr, 4) \
                    for crop in percieved_risks
                }
            perceived_prec_aw[fi] = perceived_prec_aw_f
        self.perceived_prec_aw = perceived_prec_aw
        
        
        
    # def update_climate_input(self, prec_aw_dict):
    #     """
    #     Update the climate input before the step simulation.

    #     Parameters
    #     ----------
    #     prec_aw_dict : dict
    #         A dictionary with field_id as its key and annual precipitation during
    #         growing season as its value.

    #     Returns
    #     -------
    #     None.

    #     """
    #     self.prec_aw_dict = prec_aw_dict

    def step(self):
        """
        Simulate a single timestep.

        Returns
        -------
        self (for parallel computing purpose)

        """
        self.t += 1
        self.current_year += 1
        
        ### Optimization
        # Make decisions based on CONSUMAT theory
        state = self.state
        if state == "Imitation":
            self.make_dm_imitation()
        elif state == "Social comparison":
            self.make_dm_social_comparison()
        elif state == "Repetition":
            self.make_dm_repetition()
        elif state == "Deliberation":
            self.make_dm_deliberation()
        
        # Internal experiment
        elif state == 'FixCrop':
            self.make_dm_deliberation()

        # Retrieve opt info
        dm_sols = self.dm_sols
        self.gp_status = dm_sols['gp_status']
        self.gp_MIPGap = dm_sols['gp_MIPGap']
        self.gp_report = dm_sols['gp_report']

        ### Simulation
        # Note prec_aw_dict have to be updated externally first.
        self.run_simulation()

        return self

    def run_simulation(self):

        aquifers = self.aquifers
        fields = self.fields
        wells = self.wells

        # Optimization's output
        dm_sols = self.dm_sols

        # agt dc settings
        dm_args = self.dm_args

        # Simulate fields
        for fi, field in fields.items():
            irr_depth = dm_sols[fi]["irr_depth"][:,:,[0]]
            i_crop = dm_sols[fi]["i_crop"]
            i_te = dm_sols[fi]["i_te"]
            field.step(
                irr_depth=irr_depth, i_crop=i_crop, i_te=i_te, 
                prec_aw=field.prec_aw_step[self.current_year] # Retrieve prec data
                )

        # Simulate wells (energy consumption)
        allo_r = dm_sols['allo_r']         # Well allocation ratio from optimization
        allo_r_w = dm_sols["allo_r_w"]     # Well allocation ratio from optimization
        field_ids = dm_sols["field_ids"]
        well_ids = dm_sols["well_ids"]
        self.irr_vol = sum([field.irr_vol for _, field in fields.items()])

        for k, wid in enumerate(well_ids):
            well = wells[wid]
            # Select the first year over the planning horizon from opt
            withdrawal = self.irr_vol * allo_r_w[k, 0]
            pumping_rate = sum([fields[fid].pumping_rate * allo_r[f,k,0] for f, fid in enumerate(field_ids)])
            l_pr = sum([fields[fid].l_pr * allo_r[f,k,0] for f, fid in enumerate(field_ids)])
            dwl = aquifers[well.aquifer_id].dwl * dm_args["weight_dwl"]
            well.step(withdrawal=withdrawal, dwl=dwl, pumping_rate=pumping_rate, l_pr=l_pr)

        # Calulate profit and pumping cost
        self.finance.step(fields=fields, wells=wells)

        # Collect variables for evaluation metrices
        self.profit = self.finance.profit
        yield_pct = sum([field.avg_y_y for _, field in fields.items()])/len(fields)
        self.yield_pct = yield_pct

        # Calculate satisfaction and uncertainty
        needs = self.needs
        scales = dm_args["scale"]
        self.scaled_profit = self.profit/scales["profit"]
        self.scaled_yield_pct = self.yield_pct/scales["yield_pct"]

        def func(x, alpha=1):
            return 1-np.exp(-alpha * x)
        alphas = dm_args["alphas"]
        for var, alpha in alphas.items():
            if alpha is None:
                continue
            needs[var] = func(eval(f"self.scaled_{var}"), alpha=alpha)

        eval_metric = dm_args["eval_metric"]
        satisfaction = needs[eval_metric]
        expected_sa = dm_sols["Sa"][eval_metric]
        
        # We define uncertainty to be the difference between expected_sa at the
        # previous time and satisfication this year.
        expected_sa_t_1 = self.expected_sa
        if expected_sa_t_1 is None:
            uncertainty = abs(expected_sa - satisfaction)
        else:
            uncertainty = abs(expected_sa_t_1 - satisfaction)

        # Update CONSUMAT state
        self.satisfaction = satisfaction
        self.expected_sa = expected_sa
        self.uncertainty = uncertainty
        sa_thre = self.sa_thre
        un_thre = self.un_thre
        
        if satisfaction >= sa_thre and uncertainty >= un_thre:
            self.state = "Imitation"
        elif satisfaction < sa_thre and uncertainty >= un_thre:
            self.state = "Social comparison"
        elif satisfaction >= sa_thre and uncertainty < un_thre:
            self.state = "Repetition"
        elif satisfaction < sa_thre and uncertainty < un_thre:
            self.state = "Deliberation"
        
        if self.fix_state is not None:
            self.state = self.fix_state
        
    def make_dm(self, state, dm_sols, init=False):
        """
        Make decisions based on various input parameters and states.
    
        Parameters
        ----------
        state : str
            The state of the CONSUMAT model, which can be one of the following:
            - "Imitation"
            - "Social comparison"
            - "Repetition"
            - "Deliberation"
        dm_sols : dict
            The solution dictionary from the decision-making model (dm_model).
        init : bool, optional
            Flag indicating whether this method is being run for initialization.
            Default is False.
    
        Returns
        -------
        dict
            The solution dictionary from the decision-making model (dm_model).
    
        Notes
        -----
        The method uses an optimization model (`OptModel`) to make various decisions
        based on input fields, wells, and other configurations. The method sets up constraints
        and objectives for the optimization model and then solves it to get the decisions.
        """
        aquifers = self.aquifers
        dm_args = self.dm_args
        fields = self.fields
        wells = self.wells
        
        dm = OptModel(name=self.agt_id,
                      LogToConsole=self.config_gurobi.get("LogToConsole"))
        dm.setup_ini_model(
            config=self.config,
            horizon=dm_args["horizon"],
            eval_metric=dm_args["eval_metric"],
            crop_options=self.crop_options,
            tech_options=self.tech_options,
            approx_horizon=dm_args["approx_horizon"]
            )

        perceived_prec_aw = self.perceived_prec_aw
        for fi, field in fields.items():
            block_w_interval_for_corn = field.block_w_interval_for_corn
            dm_sols_fi = dm_sols[fi]
            if init:
                # only optimize irrigation depth with others given
                dm.setup_constr_field(
                    field_id=fi,
                    prec_aw=field.prec_aw_step[self.current_year],
                    pre_i_crop=dm_sols_fi['pre_i_crop'],
                    pre_i_te=dm_sols_fi['pre_i_te'],
                    field_type=field.field_type,
                    i_crop=dm_sols_fi['i_crop'],
                    i_rainfed=None,
                    i_te=dm_sols_fi['i_te'],
                    block_w_interval_for_corn=block_w_interval_for_corn
                    )
            elif state == "FixCrop":
                dm.setup_constr_field(
                    field_id=fi,
                    prec_aw=perceived_prec_aw[fi],
                    pre_i_crop=dm_sols_fi['pre_i_crop'],
                    pre_i_te=dm_sols_fi['pre_i_te'],
                    field_type=field.field_type,
                    i_crop=dm_sols_fi['i_crop'],
                    i_rainfed=None,
                    i_te=dm_sols_fi['i_te'],
                    block_w_interval_for_corn=block_w_interval_for_corn
                    )
            elif state == "Deliberation":
                # optimize irrigation depth, crop choice, tech choice
                dm.setup_constr_field(
                    field_id=fi,
                    prec_aw=perceived_prec_aw[fi],
                    pre_i_crop=dm_sols_fi['pre_i_crop'],
                    pre_i_te=dm_sols_fi['pre_i_te'],
                    field_type=field.field_type,
                    i_crop=None,
                    i_rainfed=None,
                    i_te=None,
                    block_w_interval_for_corn=block_w_interval_for_corn
                    )
            else:
                # only optimize irrigation depth
                dm.setup_constr_field(
                    field_id=fi,
                    prec_aw=perceived_prec_aw[fi],
                    pre_i_crop=dm_sols_fi['pre_i_crop'],
                    pre_i_te=dm_sols_fi['pre_i_te'],
                    field_type=field.field_type,
                    i_crop=dm_sols_fi['i_crop'],
                    i_rainfed=dm_sols_fi['i_rainfed'],
                    i_te=dm_sols_fi['i_te'],
                    block_w_interval_for_corn=block_w_interval_for_corn
                    )

        for wi, well in wells.items():
            aquifer_id = well.aquifer_id
            proj_dwl = np.mean(aquifers[aquifer_id].dwl_list[-dm_args['n_dwl']:])
            dm.setup_constr_well(
                well_id=wi, dwl=proj_dwl, st=well.st,
                l_wt=well.l_wt, r=well.r, k=well.k,
                sy=well.sy, eff_pump=well.eff_pump,
                eff_well=well.eff_well,
                pumping_capacity=well.pumping_capacity
                )


        if init: # Inputted
            water_rights = self.water_rights
        else: # Use agent's own water rights (for social comparison and imitation)
            water_rights = self.dm_sols["water_rights"]

        for wr_id, v in self.water_rights.items():
            if v["status"]: # Check whether the wr is activated
                # Extract the water right setting from the previous opt run,
                # which we record the remaining water right fromt the previous
                # year. If the wr is newly activate in a simulation, then we
                # use the input to setup the wr.
                wr_args = water_rights.get(wr_id)
                if wr_args is None: # when first time introduce the water rights
                    dm.setup_constr_wr(
                        water_right_id=wr_id, wr=v["wr"],
                        field_id_list=v['field_id_list'],
                        time_window=v['time_window'],
                        remaining_tw=v['remaining_tw'],
                        remaining_wr=v['remaining_wr'],
                        tail_method=v['tail_method']
                        )
                else:
                    dm.setup_constr_wr(
                        water_right_id=wr_id, wr=wr_args['wr'],
                        field_id_list=wr_args['field_id_list'],
                        time_window=wr_args['time_window'],
                        remaining_tw=wr_args['remaining_tw'],
                        remaining_wr=wr_args['remaining_wr'],
                        tail_method=wr_args['tail_method']
                        )

        dm.setup_constr_finance()
        dm.setup_obj(alpha_dict=dm_args['alphas'])
        dm.finish_setup(display_summary=dm_args['display_summary'])
        dm.solve(
            keep_gp_model=dm_args['keep_gp_model'],
            keep_gp_output=dm_args['keep_gp_output'],
            display_report=dm_args['display_report']
            )
        dm_sols = dm.sols
        dm.depose_gp_env()  # Release memory
        return dm_sols

    def make_dm_deliberation(self):
        """
        Make decision under deliberation status.

        Returns
        -------
        None.

        """
        self.dm_sols = self.make_dm(state=self.state, dm_sols=self.dm_sols)

    def make_dm_repetition(self):
        """
        Make decision under repetition status.

        Returns
        -------
        None.

        """
        self.dm_sols = self.make_dm(state=self.state, dm_sols=self.dm_sols)

    def make_dm_social_comparison(self):
        """
        Make decision under social comparison status.

        Returns
        -------
        None.

        """
        agt_ids_in_network = self.agt_ids_in_network
        agts_in_network = self.agts_in_network
        # Evaluate comparable
        dm_sols_list = []
        for agt_id in agt_ids_in_network:
            # !!! Here we assume no. fields, n_c and split are the same across agents
            # Keep this for now.
            
            dm_sols = self.make_dm(
                state=self.state,
                dm_sols=agts_in_network[agt_id].dm_sols
                )
            dm_sols_list.append(dm_sols)
        objs = [s['obj'] for s in dm_sols_list]
        selected_agt_obj = max(objs)
        select_agt_index = objs.index(selected_agt_obj)
        self.selected_agt_id_in_network = agt_ids_in_network[select_agt_index]

        # Agent's original choice
        self.make_dm_repetition()
        dm_sols = self.dm_sols
        if dm_sols['obj'] >= selected_agt_obj:
            self.dm_sols = dm_sols
        else:
            self.dm_sols = dm_sols_list[select_agt_index]

    def make_dm_imitation(self):
        """
        Make decision under imitation status.

        Returns
        -------
        None.

        """
        selected_agt_id_in_network = self.selected_agt_id_in_network
        if selected_agt_id_in_network is None:
            try:    # if rngen is given in the model
                selected_agt_id_in_network = self.rngen.choice(self.agt_ids_in_network)
            except:
                selected_agt_id_in_network = np.random.choice(self.agt_ids_in_network)

        agts_in_network = self.agts_in_network

        dm_sols = self.make_dm(
            state=self.state,
            dm_sols=agts_in_network[selected_agt_id_in_network].dm_sols
            )
        self.dm_sols = dm_sols

