import torch

from fugw.mappings.dense import FUGW
from fugw.mappings.sparse import FUGWSparse
from fugw.scripts import coarse_to_fine
from fugw.utils import _make_tensor, console


class FUGWSparseBarycenter:
    """FUGW Sparse Barycenters"""

    def __init__(
        self,
        alpha_coarse=0.5,
        alpha_fine=0.5,
        rho_coarse=1.0,
        rho_fine=1.0,
        eps_coarse=1.0,
        eps_fine=1.0,
        selection_radius=1.0,
        reg_mode="joint",
        force_psd=False,
    ):
        # Save model arguments
        self.alpha_coarse = alpha_coarse
        self.alpha_fine = alpha_fine
        self.rho_coarse = rho_coarse
        self.rho_fine = rho_fine
        self.eps_coarse = eps_coarse
        self.eps_fine = eps_fine
        self.reg_mode = reg_mode
        self.force_psd = force_psd
        self.selection_radius = selection_radius

    @staticmethod
    def update_barycenter_features(plans, features_list, device):
        for i, (pi, features) in enumerate(zip(plans, features_list)):
            f = _make_tensor(features, device=device)
            weight = 1 / len(features_list)
            if features is not None:
                pi_sum = (
                    torch.sparse.sum(pi, dim=0).to_dense().reshape(-1, 1)
                    + 1e-16
                )
                acc = weight * pi.T @ f.T / pi_sum

                if i == 0:
                    barycenter_features = acc
                else:
                    barycenter_features += acc

        # Normalize barycenter features
        min_val = barycenter_features.min(dim=0, keepdim=True).values
        max_val = barycenter_features.max(dim=0, keepdim=True).values
        barycenter_features = (
            2 * (barycenter_features - min_val) / (max_val - min_val) - 1
        )
        return barycenter_features.T

    @staticmethod
    def get_dim(C):
        if isinstance(C, tuple):
            return C[0].shape[0]
        elif torch.is_tensor(C):
            return C.shape[0]

    @staticmethod
    def get_device_dtype(C):
        if isinstance(C, tuple):
            return C[0].device, C[0].dtype
        elif torch.is_tensor(C):
            return C.device, C.dtype

    def compute_all_ot_plans(
        self,
        plans,
        weights_list,
        features_list,
        geometry_embedding,
        barycenter_weights,
        barycenter_features,
        mesh_sample,
        solver,
        coarse_mapping_solver_params,
        fine_mapping_solver_params,
        selection_radius,
        sparsity_mask,
        device,
        verbose,
    ):
        new_plans = []
        new_losses = []

        for i, (features, weights) in enumerate(
            zip(features_list, weights_list)
        ):
            if verbose:
                console.log(f"Updating mapping {i + 1} / {len(weights_list)}")

            coarse_mapping = FUGW(
                alpha=self.alpha_coarse,
                rho=self.rho_coarse,
                eps=self.eps_coarse,
                reg_mode=self.reg_mode,
            )

            fine_mapping = FUGWSparse(
                alpha=self.alpha_fine,
                rho=self.rho_fine,
                eps=self.eps_fine,
                reg_mode=self.reg_mode,
            )

            _, _, sparsity_mask = coarse_to_fine.fit(
                source_features=features,
                target_features=barycenter_features,
                source_geometry_embeddings=geometry_embedding,
                target_geometry_embeddings=geometry_embedding,
                source_sample=mesh_sample,
                target_sample=mesh_sample,
                coarse_mapping=coarse_mapping,
                source_weights=weights,
                target_weights=barycenter_weights,
                coarse_mapping_solver=solver,
                coarse_mapping_solver_params=coarse_mapping_solver_params,
                coarse_pairs_selection_method="topk",
                source_selection_radius=selection_radius,
                target_selection_radius=selection_radius,
                fine_mapping=fine_mapping,
                fine_mapping_solver=solver,
                fine_mapping_solver_params=fine_mapping_solver_params,
                init_plan=plans[i] if plans is not None else None,
                sparsity_mask=sparsity_mask,
                device=device,
                verbose=verbose,
            )
            # Check for NaN values in the fine plan
            if torch.isnan(fine_mapping.pi.values()).any():
                raise ValueError("Fine plan contains NaN values")
            new_plans.append(fine_mapping.pi)
            new_losses.append(
                (
                    fine_mapping.loss,
                    fine_mapping.loss_steps,
                    fine_mapping.loss_times,
                )
            )

        return new_plans, new_losses, sparsity_mask

    def fit(
        self,
        weights_list,
        features_list,
        geometry_embedding,
        barycenter_size=None,
        init_barycenter_weights=None,
        init_barycenter_features=None,
        solver="sinkhorn",
        coarse_mapping_solver_params={},
        fine_mapping_solver_params={},
        mesh_sample=None,
        nits_barycenter=5,
        device="auto",
        callback_barycenter=None,
        verbose=False,
    ):
        """Compute barycentric features and geometry
        minimizing FUGW loss to list of distributions given as input.
        In this documentation, we refer to a single distribution as
        an a subject's or an individual's distribution.

        Parameters
        ----------
        weights_list (list of np.array): List of weights. Different individuals
            can have weights with different sizes.
        features_list (list of np.array): List of features. Individuals should
            have the same number of features n_features.
        geometry_embedding (np.array or torch.Tensor): Common geometry
            embedding of all individuals and barycenter.
        barycenter_size (int), optional:
            Size of computed barycentric features and geometry.
            Defaults to None.
        mesh_sample (np.array, optional): Sample points on which to compute
            the barycenter. Defaults to None.
        init_barycenter_weights (np.array, optional): Distribution weights
            of barycentric points. If None, points will have uniform
            weights. Defaults to None.
        init_barycenter_features (np.array, optional): np.array of size
            (barycenter_size, n_features). Defaults to None.
        solver (str, optional): Solver to use for the OT computation.
            Defaults to "sinkhorn".
        coarse_mapping_solver_params (dict, optional): Parameters for the
            coarse mapping solver. Defaults to {}.
        fine_mapping_solver_params (dict, optional): Parameters for the fine
            mapping solver. Defaults to {}.
        nits_barycenter (int, optional): Number of iterations to compute
            the barycenter. Defaults to 5.
        device: "auto" or torch.device
            if "auto": use first available gpu if it's available,
            cpu otherwise.
        callback_barycenter: callable or None
            Callback function called at the end of each barycenter step.
            It will be called with the following arguments:

                - locals (dictionary containing all local variables)

        Returns
        -------
        barycenter_weights: np.array of size (barycenter_size)
        barycenter_features: np.array of size (barycenter_size, n_features)
        barycenter_geometry: np.array of size
            (barycenter_size, barycenter_size)
        plans: list of arrays
        duals: list of (array, array)
        losses_each_bar_step: list such that l[s][i]
            is a tuple containing:
                - loss
                - loss_steps
                - loss_times
            for individual i at barycenter computation step s
        """
        if device == "auto":
            if torch.cuda.is_available():
                device = torch.device("cuda", 0)
            else:
                device = torch.device("cpu")

        if barycenter_size is None:
            barycenter_size = weights_list[0].shape[0]

        # Initialize barycenter weights, features and geometry
        if init_barycenter_weights is None:
            barycenter_weights = (
                torch.ones(barycenter_size) / barycenter_size
            ).to(device)
        else:
            barycenter_weights = _make_tensor(
                init_barycenter_weights, device=device
            )

        if init_barycenter_features is None:
            barycenter_features = torch.ones(
                (features_list[0].shape[0], barycenter_size)
            ).to(device)
            barycenter_features = barycenter_features / torch.norm(
                barycenter_features, dim=1
            ).reshape(-1, 1)
        else:
            barycenter_features = _make_tensor(
                init_barycenter_features, device=device
            )

        if not isinstance(geometry_embedding, torch.Tensor):
            geometry_embedding = _make_tensor(
                geometry_embedding, device=device
            )

        plans = None
        sparsity_mask = None
        losses_each_bar_step = []

        for idx in range(nits_barycenter):
            if verbose:
                console.log(
                    f"Barycenter iterations {idx + 1} / {nits_barycenter}"
                )

            # Transport all elements
            plans, losses, sparsity_mask = self.compute_all_ot_plans(
                plans,
                weights_list,
                features_list,
                geometry_embedding,
                barycenter_weights,
                barycenter_features,
                mesh_sample,
                solver,
                coarse_mapping_solver_params,
                fine_mapping_solver_params,
                self.selection_radius,
                sparsity_mask,
                device,
                verbose,
            )

            losses_each_bar_step.append(losses)

            # Update barycenter features and geometry
            barycenter_features = self.update_barycenter_features(
                plans, features_list, device
            )

            if callback_barycenter is not None:
                callback_barycenter(locals())

        return (
            barycenter_weights,
            barycenter_features,
            plans,
            losses_each_bar_step,
        )
