import github
import todoist
import requests
import datetime
import logging
import os
import json
import yaml
try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader
from typing import Dict

# TargetProcess specific logic

def getTargetprocessTasks(cfg:Dict) -> Dict[str, Dict]:
    tp_url = cfg['targetProcess']['url'] + '/api/v2/userstories?filter=?{}&take=100&select=%7Bid,storyName:name,bugs:Bugs.Where(EntityState.IsFinal!=true),tasks:Tasks.Where(EntityState.IsFinal!=true),project:%7Bproject.id,project.name%7D%7D&access_token={}'
    logging.info("TargetProcess URL: {}".format(tp_url))

    try:
        r = requests.get(tp_url.format(cfg['targetProcess']['query'], cfg['targetProcess']['token']))
    except Exception as e:
        logging.fatal('Cannot connect to TargetProcess')
        logging.fatal(e)
        exit(1)

    data = {}
    for task in r.json()['items']:
        task['name'] = task['storyName']
        task['url'] = "https://umarcts.tpondemand.com/RestUI/Board.aspx#page=userstory/{}".format(task['id'])
        data[formatTargetprocessTask(task)] = task
        logging.info('Task found: {}'.format(task['name']))

        for subtask in task['tasks']:
            subtask['parent'] = formatTargetprocessTask(task)
            subtask['project'] = task['project']
            subtask['url'] = "https://umarcts.tpondemand.com/RestUI/Board.aspx#page=task/{}".format(subtask['id'])
            data[formatTargetprocessTask(subtask)] = subtask
            logging.info('Subtask found: {}'.format(subtask['name']))
        for bug in task['bugs']:
            bug['parent'] = formatTargetprocessTask(task)
            bug['project'] = task['project']
            bug['url'] = "https://umarcts.tpondemand.com/RestUI/Board.aspx#page=task/{}".format(bug['id'])
            data[formatTargetprocessTask(bug)] = bug
            logging.info('Bug found: {}'.format(bug['name']))
    
    logging.debug(r.json()['items'])
    return data

def syncWithTargetprocess(api: todoist.TodoistAPI, cfg: Dict):
    tasks = getTargetprocessTasks(cfg)

    logging.info("Syncing {} TargetProcess objects to Todoist".format(len(tasks)))

    tpLabel = findOrCreateLabel(api, cfg['targetProcess']['defaultLabel'])

    if 'defaultParentProject' in cfg['targetProcess']:
        parentProject = findOrCreateProject(api, cfg['targetProcess']['defaultParentProject'])

    labels = [tpLabel['id']]

    if 'labels' in cfg['targetProcess']:
        for v in cfg['targetProcess']['labels']:
            label = findOrCreateLabel(api, v)
            labels.append(label['id'])

    item:todoist.models.Item
    for item in api['items']:
        if tpLabel['id'] in item['labels']:
            logging.debug("Item {} has label {}".format(item['content'], tpLabel['id']))
            found = False
            for k in tasks.keys():
                if k in item['content']:
                    logging.debug("Item {} matches {}".format(item['content'], k))
                    found = True
                    break
            if not found:
                logging.info("Marking {} as complete".format(item['content']))
                item.complete()
    
    for k,task in tasks.items():
        tpProject = findOrCreateProject(api, task['project']['name'])
        
        if 'defaultParentProject' in cfg['targetProcess']:
            tpProject.move(parent_id=parentProject['id'])

        item = findTaskWithContents(api, formatTargetprocessTask(task))

        taskName = "[{}]({}) - {}".format(k, task['url'], task['name'])
        if item is None:
            if 'parent' in task:
                parent = findTaskWithContents(api, task['parent'])
                logging.info("Adding task: {} with parent {}".format(taskName, task['parent']))
                api.items.add(taskName, project_id=tpProject['id'], parent_id=parent['id'], labels=labels)

            else:
                logging.info("Adding task: {}".format(taskName))
                api.items.add(taskName, project_id=tpProject['id'], labels=labels)

        else: 
            logging.info("Syncing task: {}".format(task['name']))
            taskName = "[{}]({}) - {}".format(k, task['url'], task['name'])
            api.items.update(item['id'], content=taskName)

def formatTargetprocessTask(task):
    return "TP#{}".format(task['id'])

# Github specific logic
def getGithubTasks(cfg:Dict) -> Dict[str, type(github.Issue)]:
    try:
        g = github.Github(cfg['github']['token'])
    except Exception as e:
        logging.fatal('Could not authenticate to GitHub')
        logging.fatal(e)
        exit(1)

    issues = g.search_issues(cfg['github']['query'])

    data = {}
    issue:github.Issue
    for issue in issues:
        data[formatGithubIssue(issue)] = issue
    
    logging.debug(data)

    return data

def formatGithubIssue(issue: github.Issue) -> str:
    issueType = "issue"
    if issue.pull_request is not None:
        issueType = "pr"

    return "{} {}#{}".format(issue.repository.full_name, issueType, issue.number)

def formatGithubProject(issue: github.Issue, cfg: Dict) -> str:
    projectName = issue.repository.full_name
    if 'defaultProject' in cfg['github']:
        projectName = cfg['github']['defaultProject']
    for k,v in cfg['github']['projectMap'].items():
        if k in issue.repository.full_name:
            projectName = v
    
    return projectName

def syncWithGithub(api: todoist.TodoistAPI, cfg: Dict):
    issues = getGithubTasks(cfg)

    logging.info("Syncing {} GitHub objects to Todoist".format(len(issues)))

    githubLabel = findOrCreateLabel(api, cfg['github']['defaultLabel'])

    item:todoist.models.Item
    for item in api['items']:
        if githubLabel['id'] in item['labels']:
            logging.debug("Item {} has label {}".format(item['content'], githubLabel['id']))
            found = False
            for k in issues.keys():
                if k in item['content']:
                    logging.debug("Found task: {} in {}".format(k, item['content']))
                    found = True
                    break
            if not found:
                logging.info("Marking {} as complete".format(item['content']))
                item.complete()
    
    issue:github.Issue
    for k,issue in issues.items():
        logging.debug(issue.pull_request)
        labels = [githubLabel['id']]

        if 'labels' in cfg['github']:
            for v in cfg['github']['labels']:
                label = findOrCreateLabel(api, v)
                labels.append(label['id'])

        repoProject = findOrCreateProject(api, formatGithubProject(issue, cfg))

        item = findTaskWithContents(api, formatGithubIssue(issue))

        if 'labels' in cfg['github']:
            for v in cfg['github']['labels']:
                label = findOrCreateLabel(api, v)
                labels.append(label['id'])
        
        if 'labelMap' in cfg['github']:
            for labelKey,labelList in cfg['github']['labelMap'].items():
                logging.debug("Seeing if {} is in {}".format(labelKey, k))
                if labelKey in k:
                    for a in labelList:
                        label = findOrCreateLabel(api, a)
                        labels.append(label['id'])

        if item is None:
            api.items.add("[{}]({}) - {}".format(k, "https://github.com/{}/issues/{}".format(issue.repository.full_name, issue.number), issue.title), project_id=repoProject['id'], labels=labels)
        else: 
            api.items.update(item['id'], content="[{}]({}) - {}".format(k, "https://github.com/{}/issues/{}".format(issue.repository.full_name, issue.number), issue.title))

# Todoist Specific Logic
def findOrCreateLabel(api: todoist.TodoistAPI, query: str) -> todoist.models.Label:
    logging.info("Find or create label: {}".format(query))
    for label in api['labels']:  
        if label['name'] == query:
            logging.info("Label found: {}".format(label['name']))
            return label
    
    label = api.labels.add(query)
    logging.info("Creating label: {}".format(query))
    logging.debug(label)
    return label

def findOrCreateProject(api: todoist.TodoistAPI, query: str) -> todoist.models.Project:
    logging.info("Find or create project: {}".format(query))
    for project in api['projects']:
        if project['name'] == query:
            logging.info("Project found: {}".format(project['name']))
            return project
    
    project = api.projects.add(query)
    logging.info('Creating project: {}'.format(query))
    logging.debug(project)
    return project

def findTaskWithContents(api: todoist.TodoistAPI, query: str) -> todoist.models.Item:
    logging.info("Looking for task: {}".format(query))
    for item in api['items']:
        if query in item['content']:
            logging.info("Task found: {}".format(item['content']))
            return item
    logging.info("Task NOT found: {}".format(query))
    return None

# Config Loader
def loadConfig():
    logging.info('Parsing config')
    with open(os.environ.get('NIRVANA_CONFIG', default='config.yaml'), 'r') as ymlfile:
        cfg = yaml.load(ymlfile, Loader=Loader)

    tdTokenEnv = os.environ.get('TODOIST_TOKEN', None)
    if tdTokenEnv is not None:
        if 'todoist' not in cfg:
            cfg['todoist'] = {}
        cfg['todoist']['token'] = tdTokenEnv
    tpTokenEnv = os.environ.get('TP_TOKEN', None)
    if tpTokenEnv is not None:
        if 'targetProcess' not in cfg:
            cfg['targetProcess'] = {}
        cfg['targetProcess']['token'] = tpTokenEnv
    ghTokenEnv = os.environ.get('GH_TOKEN', None)
    if ghTokenEnv is not None:
        if 'github' not in cfg:
            cfg['github'] = {}
        cfg['github']['token'] = ghTokenEnv

    return cfg



#########

def main():
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG, filename='debug.log')

    cfg = loadConfig()
    logging.debug(cfg)

    try:
        api = todoist.TodoistAPI(cfg['todoist']['token'])
    except Exception as e:
        logging.fatal('Failed to connect to Todoist')
        logging.fatal(e)
        exit(1)
    api.reset_state()
    api.sync()
    logging.info('Oh hi {}!'.format(api.state['user']['full_name']))
    logging.debug(api['items'])

    if 'github' in cfg:
        syncWithGithub(api, cfg)
        api.commit()

    if 'targetProcess' in cfg:
        syncWithTargetprocess(api, cfg)
        api.commit()
    
    logging.debug(api)

main()