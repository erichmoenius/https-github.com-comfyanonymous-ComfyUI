// @ts-check

import { api } from "./api.js";
import { ChangeTracker } from "./changeTracker.js";
import { getStorageValue, setStorageValue } from "./utils.js";

export class ComfyWorkflowManager extends EventTarget {
	#unsavedCount = 0;

	/** @type {Array<ComfyWorkflow>} */
	workflows = [];
	/** @type {Array<ComfyWorkflow>} */
	openWorkflows = [];

	get activeWorkflow() {
		return this.openWorkflows[0];
	}

	/**
	 * @param {import("./app.js").ComfyApp} app
	 */
	constructor(app) {
		super();
		this.app = app;
		ChangeTracker.init(app);
	}

	async loadWorkflows() {
		try {
			let favorites;
			const resp = await api.getUserData("workflows/.index.json");
			let info;
			if (resp.status === 200) {
				info = await resp.json();
				favorites = new Set(info?.favorites ?? []);
			} else {
				favorites = new Set();
			}

			const workflows = (await api.listUserData("workflows", true, true)).map((w) => {
				return new ComfyWorkflow(this, w[0], w.slice(1), favorites.has(w[0]));
			});

			this.workflows = workflows;
		} catch (error) {
			alert("Error loading workflows: " + (error.message ?? error));
			this.workflows = [];
		}
	}

	async saveWorkflowMetadata() {
		await api.storeUserData("workflows/.index.json", {
			favorites: [...this.workflows.filter((w) => w.isFavorite).map((w) => w.name)],
		});
	}

	/**
	 * @param {string | ComfyWorkflow | null} workflow
	 */
	setWorkflow(workflow) {
		if (workflow && typeof workflow === "string") {
			// Selected by path, i.e. on reload of last workflow
			const found = this.workflows.find((w) => w.path === workflow);
			if (found) {
				workflow = found;
				workflow.unsaved = !workflow || (getStorageValue("Comfy.PreviousWorkflowUnsaved") === "true");
			}
		}

		if (!(workflow instanceof ComfyWorkflow)) {
			// Still not found, either reloading a deleted workflow or blank
			workflow = new ComfyWorkflow(this, workflow || "Unsaved Workflow" + (this.#unsavedCount++ ? ` (${this.#unsavedCount})` : ""));
		}

		const index = this.openWorkflows.indexOf(workflow);
		if (index !== -1) {
			// Swapping to an open workflow
			this.openWorkflows.unshift(this.openWorkflows.splice(index, 1)[0]);
		} else {
			// Opening a new workflow
			this.openWorkflows.unshift(workflow);
		}

		setStorageValue("Comfy.PreviousWorkflow", this.activeWorkflow.path ?? "");
		this.dispatchEvent(new CustomEvent("change"));
	}
}

export class ComfyWorkflow {
	#name;
	#path;
	#pathParts;
	#isFavorite = false;
	/** @type {ChangeTracker | null} */
	changeTracker = null;
	unsaved = false;

	get name() {
		return this.#name;
	}

	get path() {
		return this.#path;
	}

	get pathParts() {
		return this.#pathParts;
	}

	get isFavorite() {
		return this.#isFavorite;
	}

	get isOpen() {
		return !!this.changeTracker;
	}

	/**
	 * @overload
	 * @param {ComfyWorkflowManager} manager
	 * @param {string} path
	 */
	/**
	 * @overload
	 * @param {ComfyWorkflowManager} manager
	 * @param {string} path
	 * @param {string[]} pathParts
	 * @param {boolean} isFavorite
	 */
	/**
	 * @param {ComfyWorkflowManager} manager
	 * @param {string} path
	 * @param {string[]} [pathParts]
	 * @param {boolean} [isFavorite]
	 */
	constructor(manager, path, pathParts, isFavorite) {
		this.manager = manager;
		if (pathParts) {
			this.#path = path;
			this.#pathParts = pathParts;
			this.#name = pathParts[pathParts.length - 1].replace(/\.json$/, "");
			this.#isFavorite = isFavorite;
		} else {
			this.#name = path;
			this.unsaved = true;
		}
	}

	async getWorkflowData() {
		const resp = await api.getUserData("workflows/" + this.path);
		if (resp.status !== 200) {
			alert(`Error loading user data file '${this.path}': ${resp.status} ${resp.statusText}`);
			return;
		}
		return await resp.json();
	}

	load = async () => {
		if (this.isOpen) {
			await this.manager.app.loadGraphData(this.changeTracker.activeState, true, this);
		} else {
			const data = await this.getWorkflowData();
			if (!data) return;
			await this.manager.app.loadGraphData(data, true, this);
		}
	};

	async save() {
		if (!this.path) {
			await this.#save(null, false);
		} else {
			await this.#save(this.path, true);
		}
	}

	/**
	 * @param {boolean} value
	 */
	async favorite(value) {
		try {
			await this.manager.saveWorkflowMetadata();
			this.#isFavorite = value;
			this.manager.dispatchEvent(new CustomEvent("favorite", { detail: this }));
		} catch (error) {
			alert("Error favoriting workflow " + this.name + "\n" + (error.message ?? error));
		}
	}

	/**
	 * @param {string} path
	 */
	async rename(path) {
		let res = await api.moveUserData("workflows/" + this.path, "workflows/" + path);

		if (res.status === 409) {
			if (!confirm(`Workflow '${path}' already exists, do you want to overwrite it?`)) return res;
			res = await api.moveUserData("workflows/" + this.path, "workflows/" + path, { overwrite: true });
		}

		this.#updatePath(path);
		return res;
	}

	async insert() {
		const data = await this.getWorkflowData();
		if (!data) return;

		const old = localStorage.getItem("litegrapheditor_clipboard");
		const graph = new LGraph(data);
		const canvas = new LGraphCanvas(null, graph, { skip_events: true, skip_render: true });
		canvas.selectNodes();
		canvas.copyToClipboard();
		this.manager.app.canvas.pasteFromClipboard();
		localStorage.setItem("litegrapheditor_clipboard", old);
	}

	async delete() {
		if (this.isFavorite) {
			await this.favorite(false);
		}
		await api.deleteUserData(this.path);
		this.manager.dispatchEvent(new CustomEvent("delete", { detail: this }));
	}

	track() {
		if (this.changeTracker) {
			this.changeTracker.restoreViewport();
		} else {
			this.changeTracker = new ChangeTracker(this);
		}
	}

	/**
	 * @param {string} path
	 */
	#updatePath(path) {
		this.#path = path;
		if (!path.includes("\\")) {
			this.#pathParts = path.split("/");
		} else {
			this.#pathParts = path.split("\\");
		}
		this.manager.dispatchEvent(new CustomEvent("rename", { detail: this }));
	}

	/**
	 * @param {string|null} path
	 * @param {boolean} overwrite
	 */
	async #save(path, overwrite) {
		if (!path) {
			path = prompt("Save workflow as:", this.path ?? "workflow");
			if (!path) return;
		}

		if (!path.toLowerCase().endsWith(".json")) {
			path += ".json";
		}

		const p = await this.manager.app.graphToPrompt();
		const json = JSON.stringify(p.workflow, null, 2);
		const res = await api.storeUserData("workflows/" + path, json, { stringify: false, throwOnError: false, overwrite });
		if (res.status === 409) {
			if (!confirm(`Workflow '${path}' already exists, do you want to overwrite it?`)) return;
			await api.storeUserData("workflows/" + path, json, { stringify: false });
			this.#updatePath(path);
		} else if (path === this.path && this.isOpen) {
			this.unsaved = false;
			this.manager.dispatchEvent(new CustomEvent("save", { detail: this }));
		}
	}
}

